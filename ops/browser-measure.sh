#!/usr/bin/env bash
# Measures a headed Chromium on the Jetson (browser B1): the image of browser/Dockerfile, Xvfb,
# x11vnc and websockify, Chromium with the spec's switches, and Playwright attached over CDP on
# 127.0.0.1. Everything runs in throwaway containers of its own, on an internal network of its
# own, against the static pages of tests/browser_pages/ served by a container of the same image.
# It never touches the app's stack and never reaches the internet once the image is built.
#
#   ops/browser-measure.sh                     dry: prints what it would run, runs nothing
#   ops/browser-measure.sh --real              builds, measures, copies the results, cleans up
#   ops/browser-measure.sh --real --step build|sandbox|matrix|edge|hog|fetch|clean
#                                              one step at a time (keep the same --dir)
#   options: --host H (default jetson), --dir b1spike-<name> (the folder in ~ and the prefix of
#   every container, network and image tag; default b1spike-<random>), --hold S (seconds each
#   page is held with the VNC client connected, default 120), --tabs 1,2 (tab counts), --pages
#   blank,form,workday,slow,hog, --mem 1600m (container limit for the matrix), --hog-mem 1152m
#   (limit for the hog run), --seccomp FILE (a seccomp profile to try for Chromium's sandbox),
#   --out DIR (where results land locally, default tasks/evidence/browser-b1).
#
# What it answers: image size, cold start to a usable CDP, peak memory of the container (sampled
# every 0.2 s: kernel 5.15 has no memory.peak) and per process group (Chromium, Xvfb, x11vnc,
# websockify, the Playwright driver; proportional set size) per page and tab count, load and
# fill times, /dev/shm use, whether Chromium's sandbox works in the container and under which
# seccomp profile, set_input_files and a file chooser over CDP, the snapshot API with refs, the
# user agent and client hints, a session cookie across a close and a start, a dead profile lock,
# Chromium killed under Playwright, and a page that eats memory until the container's limit.
# Before every measurement the host must have 2,500 MiB available, else it waits a minute, ten
# times at most, then stops. A problem shows as a line starting with PROBLEM and exit status 1.
set -euo pipefail

HOST=jetson
DIR=""
REAL=0
STEP=all
HOLD=120
TABS=1,2
PAGES=blank,form,workday,slow,hog
MEM=1600m
HOG_MEM=1152m
SECCOMP=""
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HERE/tasks/evidence/browser-b1"

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

while (($#)); do
  case "$1" in
    --real) REAL=1 ;;
    --host) HOST="$2"; shift ;;
    --dir) DIR="$2"; shift ;;
    --step) STEP="$2"; shift ;;
    --hold) HOLD="$2"; shift ;;
    --tabs) TABS="$2"; shift ;;
    --pages) PAGES="$2"; shift ;;
    --mem) MEM="$2"; shift ;;
    --hog-mem) HOG_MEM="$2"; shift ;;
    --seccomp) SECCOMP="$2"; shift ;;
    --out) OUT="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[[ -n "$DIR" ]] || DIR="b1spike-$(od -An -N4 -tx4 /dev/urandom | tr -d ' \n')"
if [[ "$DIR" == */* || "$DIR" == *..* || ! "$DIR" =~ ^b1spike-[A-Za-z0-9_-]+$ ]]; then
  echo "--dir must match b1spike-[A-Za-z0-9_-]+ (no / and no ..), got: $DIR" >&2
  exit 2
fi
[[ "$HOLD" =~ ^[0-9]{1,4}$ ]] || { echo "--hold must be seconds" >&2; exit 2; }
[[ "$TABS" =~ ^[1-3](,[1-3])*$ ]] || { echo "--tabs takes 1 to 3, comma separated" >&2; exit 2; }
[[ "$PAGES" =~ ^[a-z]+(,[a-z]+)*$ ]] || { echo "--pages takes names, comma separated" >&2; exit 2; }
for m in "$MEM" "$HOG_MEM"; do
  [[ "$m" =~ ^([0-9]+)m$ ]] && ((BASH_REMATCH[1] <= 1600)) || { echo "memory limits are <N>m, at most 1600m" >&2; exit 2; }
done
case "$STEP" in all|build|sandbox|matrix|edge|hog|fetch|clean) ;; *) echo "unknown --step $STEP" >&2; exit 2 ;; esac

SUFFIX="${DIR#b1spike-}"
TAG="b1spike-browser:${SUFFIX}"
NET="${DIR}-net"
PAGES_C="${DIR}-pages"
PAGES_URL="http://${PAGES_C}:8000"
COMMON="--network ${NET} --cpus 3 --shm-size 128m --cap-drop ALL --security-opt no-new-privileges \
 -e B1_PAGES_URL=${PAGES_URL} -v \$HOME/${DIR}/b1:/b1:ro -v \$HOME/${DIR}/out:/out"

# r CMD: runs CMD on the Jetson with --real, prints it otherwise.
r() {
  if ((REAL)); then
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "$1"
  else
    printf 'dry (%s): %s\n' "$HOST" "$1"
  fi
}

guard() {
  ((REAL)) || { echo "dry: would check free -m for 2500 MiB available"; return 0; }
  local i avail
  for i in 1 2 3 4 5 6 7 8 9 10; do
    avail="$(r "free -m | awk '/^Mem:/{print \$7}'")"
    if ((avail >= 2500)); then
      echo "available ${avail} MiB"
      return 0
    fi
    echo "available ${avail} MiB, under 2500: waiting a minute (${i}/10)"
    sleep 60
  done
  echo "PROBLEM: the Jetson stayed under 2500 MiB available, measurement stopped"
  exit 1
}

# The driver and the pages server, written into the staging folder copied to the Jetson.
write_driver() {
  local dest="$1"
  cat >"$dest/serve.py" <<'PY'
"""The pages server of browser B1: the static pages, a slow endpoint, the request headers, a
session cookie and an upload sink that answers with what it received (sizes, never content)."""

import http.server
import json
import sys
import time
import urllib.parse

ROOT = sys.argv[1]


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, *args):
        pass

    def send(self, body, ctype="application/json", headers=()):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-CH", "Sec-CH-UA-Platform-Version, Sec-CH-UA-Full-Version-List, Sec-CH-UA-Arch, Sec-CH-UA-Model")
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path == "/slow":
            time.sleep(min(int(query.get("ms", ["3000"])[0]), 20000) / 1000)
            if query.get("type", ["js"])[0] == "js":
                return self.send("window.__slowLoaded = (window.__slowLoaded || 0) + 1;", "text/javascript")
            return self.send(json.dumps({"ok": True}))
        if url.path == "/headers":
            return self.send(json.dumps({k.lower(): v for k, v in self.headers.items()}))
        if url.path == "/cookie":
            value = query.get("v", ["x"])[0]
            return self.send("<p>cookie set</p>", "text/html", [("Set-Cookie", f"b1session={value}; Path=/; SameSite=Lax")])
        return super().do_GET()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send(json.dumps({"bytes": len(body), "pdf": body.count(b"%PDF-"), "files": body.count(b'filename="')}))


http.server.ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
PY
  cat >"$dest/measure.py" <<'PY'
"""The measurement driver of browser B1, run inside the browser container.

measure.py probe            starts Xvfb and Chromium (sandbox on), reports the namespaces; exit 3 if Chromium cannot start
measure.py matrix P T H     pages P (comma list), tab counts T, hold H seconds each, with a VNC client connected
measure.py edge             uploads, snapshot API, user agent, cookie across restart, dead lock, Chromium killed
measure.py hog              the uncapped hog page until the container's limit
"""

import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request

OUT = "/out"
PAGES = os.environ.get("B1_PAGES_URL", "http://127.0.0.1:8000")
CDP = "http://127.0.0.1:9222"
CG = "/sys/fs/cgroup"
T0 = time.monotonic()
MIB = 1048576


def now():
    return time.monotonic()


def read(path, default=""):
    try:
        with open(path) as handle:
            return handle.read()
    except OSError:
        return default


def kv(path):
    out = {}
    for line in read(path).splitlines():
        key, _, value = line.partition(" ")
        if value.strip().isdigit():
            out[key] = int(value)
    return out


def mib(value):
    return round(value / MIB, 1)


def group_of(cmd):
    if "chromium" in cmd:
        return "chromium"
    if cmd.startswith("Xvfb"):
        return "xvfb"
    if "x11vnc" in cmd:
        return "x11vnc"
    if "websockify" in cmd:
        return "websockify"
    if "playwright" in cmd or "measure.py" in cmd:
        return "driver"
    return "other"


def groups():
    sizes, counts = {}, {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        cmd = read(f"/proc/{pid}/cmdline").replace("\0", " ").strip()
        match = re.search(r"^Pss:\s+(\d+) kB", read(f"/proc/{pid}/smaps_rollup"), re.M)
        if not cmd or not match:
            continue
        name = group_of(cmd)
        sizes[name] = sizes.get(name, 0) + int(match.group(1)) * 1024
        counts[name] = counts.get(name, 0) + 1
    return sizes, counts


def shm_used():
    st = os.statvfs("/dev/shm")
    return (st.f_blocks - st.f_bfree) * st.f_frsize


class Sampler(threading.Thread):
    """Peaks of the container's cgroup (every 0.2 s) and of each process group (every 0.6 s)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.cg = 0
            self.anon = 0
            self.shm = 0
            self.sizes = {}
            self.procs = 0

    def run(self):
        tick = 0
        while True:
            current, shm = int(read(f"{CG}/memory.current", "0") or 0), shm_used()
            with self.lock:
                self.cg, self.shm = max(self.cg, current), max(self.shm, shm)
            if tick % 3 == 0:
                sizes, counts = groups()
                anon = kv(f"{CG}/memory.stat").get("anon", 0)
                with self.lock:
                    for key, value in sizes.items():
                        self.sizes[key] = max(self.sizes.get(key, 0), value)
                    self.sizes["all"] = max(self.sizes.get("all", 0), sum(sizes.values()))
                    self.procs = max(self.procs, counts.get("chromium", 0))
                    self.anon = max(self.anon, anon)
            tick += 1
            time.sleep(0.2)

    def result(self):
        with self.lock:
            return {
                "cgroup_peak_mib": mib(self.cg),
                "anon_peak_mib": mib(self.anon),
                "shm_peak_mib": mib(self.shm),
                "pss_peak_mib": {k: mib(v) for k, v in sorted(self.sizes.items())},
                "chromium_processes_peak": self.procs,
            }


def recvn(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise OSError("vnc closed")
        data += chunk
    return data


class Vnc(threading.Thread):
    """A live view client on x11vnc: asks for Tight or ZRLE updates every 100 ms, as noVNC does,
    and discards them, so x11vnc encodes the screen the whole time a page is held."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop = False
        self.received = 0
        self.error = ""

    def run(self):
        try:
            sock = socket.create_connection(("127.0.0.1", 5900), timeout=5)
            recvn(sock, 12)
            sock.sendall(b"RFB 003.008\n")
            types = recvn(sock, recvn(sock, 1)[0])
            if 1 not in types:
                raise OSError(f"no None security type: {list(types)}")
            sock.sendall(b"\x01")
            recvn(sock, 4)
            sock.sendall(b"\x01")
            width, height = struct.unpack(">HH", recvn(sock, 4))
            recvn(sock, 16)
            recvn(sock, struct.unpack(">I", recvn(sock, 4))[0])
            sock.sendall(struct.pack(">BxH3i", 2, 3, 7, 16, 0))
            sock.settimeout(0.1)
            last, incremental = 0.0, 0
            while not self.stop:
                if now() - last > 0.1:
                    sock.sendall(struct.pack(">BBHHHH", 3, incremental, 0, 0, width, height))
                    last, incremental = now(), 1
                try:
                    data = sock.recv(1 << 20)
                    if not data:
                        raise OSError("vnc closed")
                    self.received += len(data)
                except socket.timeout:
                    pass
            sock.close()
        except OSError as error:
            self.error = str(error)


procs = {}


def start_display():
    began = now()
    procs["xvfb"] = subprocess.Popen(["Xvfb", ":1", "-screen", "0", "1440x900x24", "-nolisten", "tcp"],
                                     stdout=subprocess.DEVNULL, stderr=open(f"{OUT}/xvfb.log", "ab"))
    while not os.path.exists("/tmp/.X11-unix/X1"):
        if now() - began > 15:
            raise SystemExit("Xvfb did not start")
        time.sleep(0.05)
    shown = now() - began
    procs["x11vnc"] = subprocess.Popen(["x11vnc", "-display", ":1", "-localhost", "-rfbport", "5900", "-viewonly",
                                        "-shared", "-forever", "-nopw", "-quiet"],
                                       stdout=subprocess.DEVNULL, stderr=open(f"{OUT}/x11vnc.log", "ab"))
    procs["websockify"] = subprocess.Popen(["websockify", "--web", "/usr/share/novnc", "6080", "127.0.0.1:5900"],
                                           stdout=subprocess.DEVNULL, stderr=open(f"{OUT}/websockify.log", "ab"))
    return round(shown, 2)


def switches(profile):
    return ["chromium", f"--user-data-dir={profile}", "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=9222", "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled", "--window-size=1440,900", "--start-maximized",
            "--lang=fr-FR", "--password-store=basic", "--disable-features=Translate",
            # A first tab on about:blank: without it the new tab page loads late and can interrupt
            # the first navigation of the driver.
            "about:blank"]


def write_prefs(profile, extra=None):
    os.makedirs(f"{profile}/Default", exist_ok=True)
    path = f"{profile}/Default/Preferences"
    if not os.path.exists(path):
        with open(path, "w") as handle:
            json.dump({"session": {"restore_on_startup": 1}, "credentials_enable_service": False,
                       "profile": {"password_manager_enabled": False}, **(extra or {})}, handle)


def start_chromium(profile, timeout=30):
    """Starts Chromium and waits for its CDP endpoint; returns (process or None, seconds)."""
    write_prefs(profile)
    began = now()
    proc = subprocess.Popen(switches(profile), stdout=subprocess.DEVNULL, start_new_session=True,
                            stderr=open(f"{OUT}/chromium.log", "ab"))
    while now() - began < timeout:
        if proc.poll() is not None:
            return None, round(now() - began, 2)
        try:
            urllib.request.urlopen(CDP + "/json/version", timeout=1).read()
            procs["chromium"] = proc
            return proc, round(now() - began, 2)
        except OSError:
            time.sleep(0.05)
    os.killpg(proc.pid, signal.SIGKILL)
    return None, round(now() - began, 2)


def stop_chromium(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(15)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(0.5)


def log_tail(name, lines=8):
    return read(f"{OUT}/{name}").splitlines()[-lines:]


def namespaces():
    mine = os.readlink("/proc/self/ns/user")
    seen = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        cmd = read(f"/proc/{pid}/cmdline").replace("\0", " ")
        if "chromium" not in cmd:
            continue
        kind = re.search(r"--type=([a-z-]+)", cmd)
        try:
            ns = os.readlink(f"/proc/{pid}/ns/user")
        except OSError:
            continue
        seen.setdefault(kind.group(1) if kind else "browser", set()).add(ns != mine)
    return {k: sorted(v) for k, v in seen.items()}


def save(name, data):
    with open(f"{OUT}/{name}.json", "w") as handle:
        json.dump(data, handle, indent=1)
    print(json.dumps(data), flush=True)


def playwright():
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()


def attach(pw):
    began = now()
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
    return browser, browser.contexts[0], round(now() - began, 2)


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def fill_form(page, paced):
    """Fills the ATS form; paced types 100 ms per character as the tool layer will."""
    values = {"#first_name": "Ada", "#last_name": "Example", "#email": "e2e-b1@example.org",
              "#phone": "+33 6 00 00 00 00", "#location": "Paris, France", "#linkedin": "https://example.org/in/ada",
              "#why": "I build agents that run in production and I like evaluating them."}
    began = now()
    for selector, value in values.items():
        if paced:
            page.locator(selector).press_sequentially(value, delay=100)
        else:
            page.fill(selector, value)
    page.select_option("#source", "LinkedIn")
    page.select_option("#notice", "2 months")
    page.click("div[data-name=authorised] button[data-option=yes]")
    page.click("div[data-name=sponsorship] button[data-option=no]")
    page.check("#consent")
    return round(now() - began, 2)


def upload(page):
    path = "/tmp/b1-cv.pdf"
    with open(path, "wb") as handle:
        handle.write(PDF)
    result = {}
    began = now()
    try:
        page.set_input_files("#resume", path)
        page.wait_for_function("document.getElementById('resume-status').dataset.ok !== undefined", timeout=10000)
        result["set_input_files"] = page.inner_text("#resume-status")
    except Exception as error:  # noqa: BLE001 - the measurement reports any failure
        result["set_input_files"] = f"FAILED {type(error).__name__}: {str(error)[:160]}"
    try:
        with page.expect_file_chooser(timeout=10000) as chooser:
            page.click("#attach-cover")
        chooser.value.set_files(path)
        page.wait_for_function("document.getElementById('cover-status').dataset.ok !== undefined", timeout=10000)
        result["file_chooser"] = page.inner_text("#cover-status")
    except Exception as error:  # noqa: BLE001
        result["file_chooser"] = f"FAILED {type(error).__name__}: {str(error)[:160]}"
    try:
        page.click("button[type=submit]")
        page.wait_for_function("document.getElementById('result').dataset.json !== undefined", timeout=10000)
        result["server_received"] = json.loads(page.get_attribute("#result", "data-json"))
    except Exception as error:  # noqa: BLE001
        result["server_received"] = f"FAILED {type(error).__name__}: {str(error)[:160]}"
    result["upload_s"] = round(now() - began, 2)
    return result


def act(name, page):
    if name == "form":
        loaded = now()
        out = {"fill_s": fill_form(page, paced=False)}
        page.reload()
        out["fill_paced_s"] = fill_form(page, paced=True)
        out.update(upload(page))
        out["form_total_s"] = round(now() - loaded, 2)
        return out
    if name == "workday":
        began = now()
        for field in page.locator("section.step.active input:not([role=combobox])").all()[:24]:
            field.fill("2020-01-01" if field.get_attribute("type") == "date" else "e2e-b1@example.org"
                       if field.get_attribute("type") == "email" else "Answer")
        page.click("#next")
        return {"fill_s": round(now() - began, 2)}
    if name == "slow":
        began = now()
        page.wait_for_selector("body[data-ready=true]", timeout=30000)
        return {"ready_after_load_s": round(now() - began, 2)}
    if name == "hog":
        page.wait_for_selector("#allocated[data-done=true]", timeout=60000)
    return {}


def url_of(name):
    return {"blank": "about:blank", "hog": f"{PAGES}/hog.html?cap=320"}.get(name, f"{PAGES}/{name}.html")


def matrix(pages, tabs, hold):
    display_s = start_display()
    sampler = Sampler()
    sampler.start()
    pw = playwright()
    results = []
    idle = None
    for count in tabs:
        for name in pages:
            # A fresh profile per row: the spec's "continue where you left off" would otherwise
            # restore the previous row's tabs into this one.
            profile = f"/profiles/matrix-{name}-{count}"
            proc, start_s = start_chromium(profile)
            if proc is None:
                results.append({"page": name, "tabs": count, "error": "chromium did not start", "log": log_tail("chromium.log")})
                continue
            browser, context, attach_s = attach(pw)
            if idle is None:
                cold = round(now() - T0, 2)
                time.sleep(5)
                sampler.reset()
                time.sleep(3)
                idle = sampler.result()
                idle["cold_start_to_cdp_s"] = cold
                idle["xvfb_ready_s"] = display_s
            sampler.reset()
            vnc = Vnc()
            vnc.start()
            tabs_open = [context.pages[0]] + [context.new_page() for _ in range(count - 1)]
            loads, extra = [], {}
            try:
                for index, page in enumerate(tabs_open):
                    began = now()
                    page.goto(url_of(name), wait_until="load", timeout=60000)
                    loads.append(round(now() - began, 2))
                    if index == 0:
                        extra = act(name, page)
                if count == 1 and name != "blank":
                    tabs_open[0].screenshot(path=f"{OUT}/{name}.png")
                time.sleep(hold)
            except Exception as error:  # noqa: BLE001 - the row records it and the matrix goes on
                extra["error"] = f"{type(error).__name__}: {str(error)[:300]}"
            vnc.stop = True
            vnc.join(3)
            row = {"page": name, "tabs": count, "profile": profile, "hold_s": hold, "chromium_start_s": start_s, "attach_s": attach_s,
                   "load_s": loads, "vnc_bytes": vnc.received, "vnc_error": vnc.error, **extra, **sampler.result()}
            results.append(row)
            print(json.dumps(row), flush=True)
            # Appended row by row, so the matrix can be run in parts and a failure keeps the rest.
            with open(f"{OUT}/matrix.jsonl", "a") as handle:
                handle.write(json.dumps({"row": row}) + "\n")
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
            stop_chromium(proc)
    with open(f"{OUT}/matrix.jsonl", "a") as handle:
        handle.write(json.dumps({"idle": idle}) + "\n")


def probe():
    start_display()
    proc, start_s = start_chromium("/profiles/probe", timeout=20)
    if proc is None:
        save("probe", {"started": False, "start_s": start_s, "log": log_tail("chromium.log", 12)})
        sys.exit(3)
    pw = playwright()
    browser, context, _ = attach(pw)
    page = context.pages[0]
    page.goto(f"{PAGES}/form.html")
    sandbox = ""
    try:
        page.goto("chrome://sandbox")
        sandbox = page.inner_text("body")[:1500]
    except Exception as error:  # noqa: BLE001
        sandbox = f"FAILED {error}"
    save("probe", {"started": True, "start_s": start_s, "new_user_namespace_by_process_type": namespaces(),
                   "chrome_sandbox_page": sandbox, "version": browser.version, "log": log_tail("chromium.log", 6)})
    stop_chromium(proc)


def snapshot_for_ai(page):
    """Playwright MCP's snapshot (page._snapshotForAI in JavaScript): the Python client of 1.55
    has no wrapper, so the protocol method of the driver is called on the page's channel."""
    try:
        answer = page._sync(page._impl_obj._channel.send("snapshotForAI", None, {"timeout": 10000}))
    except Exception:  # noqa: BLE001 - older protocols take no timeout
        answer = page._sync(page._impl_obj._channel.send("snapshotForAI", None, {}))
    return answer if isinstance(answer, str) else answer.get("snapshot", json.dumps(answer))


def snapshot_api(page):
    from importlib.metadata import version

    found = {"playwright_version": version("playwright")}
    for name, call in [
        ("channel snapshotForAI", lambda: snapshot_for_ai(page)),
        ("locator('body').aria_snapshot()", lambda: page.locator("body").aria_snapshot()),
        ("page.accessibility.snapshot()", lambda: json.dumps(page.accessibility.snapshot())),
    ]:
        try:
            text = call()
            text = text if isinstance(text, str) else json.dumps(text)
            found[name] = {"chars": len(text), "has_refs": "[ref=" in text}
            with open(f"{OUT}/snapshot-{re.sub(r'[^a-z_]+', '-', name.split('(')[0].lower()).strip('-')}.txt", "w") as handle:
                handle.write(text[:4000])
        except Exception as error:  # noqa: BLE001
            found[name] = f"FAILED {type(error).__name__}: {str(error)[:160]}"
    return found


def edge():
    out = {"display_s": start_display()}
    pw = playwright()
    proc, out["start_s"] = start_chromium("/profiles/edge")
    browser, context, out["attach_s"] = attach(pw)
    page = context.pages[0]
    page.goto(f"{PAGES}/headers")
    out["request_headers"] = {k: v for k, v in json.loads(page.inner_text("body")).items()
                              if k.startswith("sec-ch") or k in ("user-agent", "accept-language")}
    page.goto(f"{PAGES}/form.html")
    out["navigator"] = page.evaluate("""async () => ({ua: navigator.userAgent, platform: navigator.platform,
        webdriver: navigator.webdriver, languages: navigator.languages,
        brands: navigator.userAgentData && navigator.userAgentData.brands,
        high: navigator.userAgentData && await navigator.userAgentData.getHighEntropyValues(
            ['architecture', 'model', 'platformVersion', 'fullVersionList', 'bitness']),
        tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
        webgl: (() => { const g = document.createElement('canvas').getContext('webgl');
            const d = g && g.getExtension('WEBGL_debug_renderer_info');
            return d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : null; })()})""")
    out["snapshot"] = snapshot_api(page)
    out["upload"] = upload(page)
    page.screenshot(path=f"{OUT}/form-uploaded.png", full_page=True)

    began = now()
    page.goto(f"{PAGES}/workday.html")
    killed = now()
    os.killpg(proc.pid, signal.SIGKILL)
    while now() - killed < 15:
        try:
            page.evaluate("1")
            time.sleep(0.05)
        except Exception as error:  # noqa: BLE001
            out["killed"] = {"noticed_s": round(now() - killed, 2), "error": f"{type(error).__name__}: {str(error)[:120]}"}
            break
    else:
        out["killed"] = {"noticed_s": None}
    proc.wait()

    profile = "/profiles/edge"
    out["lock_after_kill"] = {"singleton_lock_left": os.path.lexists(f"{profile}/SingletonLock"),
                              "lock_target": os.readlink(f"{profile}/SingletonLock") if os.path.islink(f"{profile}/SingletonLock") else None}
    proc, start_s = start_chromium(profile, timeout=20)
    out["lock_after_kill"]["restart_without_clearing"] = {"started": proc is not None, "start_s": start_s}
    if proc is None:
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            try:
                os.unlink(f"{profile}/{name}")
            except FileNotFoundError:
                pass
        proc, start_s = start_chromium(profile)
        out["lock_after_kill"]["restart_after_clearing"] = {"started": proc is not None, "start_s": start_s}
    stop_chromium(proc)

    restore = "/profiles/restore"
    proc, _ = start_chromium(restore)
    browser, context, _ = attach(pw)
    page = context.pages[0]
    page.goto(f"{PAGES}/cookie?v=b1token")
    page.goto(f"{PAGES}/form.html")
    page.fill("#first_name", "Ada")
    time.sleep(2)
    cdp = browser.new_browser_cdp_session()
    began = now()
    try:
        cdp.send("Browser.close")
    except Exception:  # noqa: BLE001
        pass
    proc.wait(30)
    close_s = round(now() - began, 2)
    began = now()
    proc, start_s = start_chromium(restore)
    browser, context, _ = attach(pw)
    restored = []
    while now() - began < 20:
        restored = [p.url for p in context.pages]
        if any("form.html" in u for u in restored):
            break
        time.sleep(0.1)
    cookies = [c for c in context.cookies() if c["name"] == "b1session"]
    out["restore"] = {"close_s": close_s, "start_s": start_s, "restored_tab_s": round(now() - began, 2),
                      "restored_urls": restored, "session_cookie_kept": bool(cookies),
                      "cookie_expires": cookies[0]["expires"] if cookies else None}
    stop_chromium(proc)

    # A lock left by another container (another host name), as after a container is recreated
    # on a persisted profile volume.
    foreign = "/profiles/foreign"
    os.makedirs(foreign, exist_ok=True)
    os.symlink("b1spike-gone-4242", f"{foreign}/SingletonLock")
    proc, start_s = start_chromium(foreign, timeout=20)
    out["foreign_lock"] = {"started": proc is not None, "start_s": start_s, "log": log_tail("chromium.log", 2)}
    if proc is None:
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            if os.path.lexists(f"{foreign}/{name}"):
                os.unlink(f"{foreign}/{name}")
        proc, start_s = start_chromium(foreign)
        out["foreign_lock"]["after_clearing"] = {"started": proc is not None, "start_s": start_s}
    if proc is not None:
        stop_chromium(proc)

    # Client hints and userAgentData exist only in a secure context: a page on 127.0.0.1 is one,
    # the pages server on the internal network over http is not. The accept language comes from
    # the preference intl.accept_languages here (--lang is ignored on Linux).
    server = subprocess.Popen(["python3", "/b1/serve.py", "/tmp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hints = "/profiles/hints"
    write_prefs(hints, {"intl": {"accept_languages": "fr-FR,fr,en-US,en"}})
    proc, _ = start_chromium(hints)
    browser, context, _ = attach(pw)
    page = context.pages[0]
    page.goto("http://127.0.0.1:8000/headers")
    page.reload()
    out["secure_context_headers"] = {k: v for k, v in json.loads(page.inner_text("body")).items()
                                     if k.startswith("sec-ch") or k in ("user-agent", "accept-language")}
    out["secure_context_navigator"] = page.evaluate("""async () => ({platform: navigator.platform,
        brands: navigator.userAgentData.brands, mobile: navigator.userAgentData.mobile,
        high: await navigator.userAgentData.getHighEntropyValues(['architecture', 'bitness', 'model',
            'platformVersion', 'fullVersionList'])})""")
    stop_chromium(proc)
    server.terminate()
    save("edge", out)


def hog():
    start_display()
    pw = playwright()
    proc, _ = start_chromium("/profiles/hog")
    browser, context, _ = attach(pw)
    sampler = Sampler()
    sampler.start()
    before = kv(f"{CG}/memory.events")
    page = context.pages[0]
    page.goto(f"{PAGES}/hog.html", wait_until="commit")
    out = {"limit_mib": mib(int(read(f"{CG}/memory.max", "0").strip() or 0))}
    began = now()
    try:
        page.wait_for_event("crash", timeout=180000)
        out["page_crashed_s"] = round(now() - began, 2)
    except Exception as error:  # noqa: BLE001
        out["page_crashed_s"] = f"no crash event: {type(error).__name__}"
    time.sleep(2)
    after = kv(f"{CG}/memory.events")
    out["oom_kill"] = after.get("oom_kill", 0) - before.get("oom_kill", 0)
    out["oom_events"] = after.get("oom", 0) - before.get("oom", 0)
    out["browser_alive"] = proc.poll() is None
    try:
        fresh = context.new_page()
        fresh.goto(f"{PAGES}/form.html")
        out["new_tab_after_crash"] = fresh.title()
    except Exception as error:  # noqa: BLE001
        out["new_tab_after_crash"] = f"FAILED {type(error).__name__}"
    out.update(sampler.result())
    save("hog", out)
    stop_chromium(proc)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    mode = sys.argv[1]
    if mode == "matrix":
        matrix(sys.argv[2].split(","), [int(t) for t in sys.argv[3].split(",")], int(sys.argv[4]))
    elif mode == "probe":
        probe()
    elif mode == "edge":
        edge()
    elif mode == "hog":
        hog()
    for proc in procs.values():
        if proc.poll() is None:
            proc.terminate()
PY
}

step_build() {
  local stage
  stage="$(mktemp -d)"
  mkdir -p "$stage/b1" "$stage/pages"
  cp "$HERE/browser/Dockerfile" "$stage/Dockerfile"
  cp "$HERE"/tests/browser_pages/*.html "$stage/pages/"
  write_driver "$stage/b1"
  [[ -n "$SECCOMP" ]] && cp "$SECCOMP" "$stage/seccomp.json"
  r "mkdir -p \$HOME/${DIR}/out && chmod 777 \$HOME/${DIR}/out"
  if ((REAL)); then
    rsync -a "$stage/" "${HOST}:${DIR}/"
  else
    echo "dry: rsync $stage/ ${HOST}:${DIR}/"
  fi
  rm -rf "$stage"
  r "docker image inspect debian:bookworm-slim >/dev/null 2>&1 || touch \$HOME/${DIR}/.pulled-base"
  r "cd \$HOME/${DIR} && start=\$(date +%s) && DOCKER_BUILDKIT=0 docker build -q --rm --force-rm --memory 1600m -t ${TAG} . && echo build_s=\$((\$(date +%s)-start))"
  r "docker image inspect -f 'image_bytes={{.Size}}' ${TAG}; docker run --rm --name ${DIR}-ver --memory 256m --cpus 1 ${TAG} sh -c 'dpkg-query -W chromium xvfb x11vnc websockify novnc; /opt/venv/bin/pip list 2>/dev/null | grep -i ^playwright'"
  r "docker network inspect ${NET} >/dev/null 2>&1 || docker network create --internal ${NET} >/dev/null"
  r "docker inspect ${PAGES_C} >/dev/null 2>&1 || docker run -d --rm --name ${PAGES_C} --network ${NET} --memory 128m --cpus 1 \
     -v \$HOME/${DIR}/pages:/pages:ro -v \$HOME/${DIR}/b1:/b1:ro ${TAG} python3 /b1/serve.py /pages >/dev/null"
}

# run NAME MEMORY SECCOMP_OPTION ARGS...: one browser container, removed when it ends.
run() {
  local name="$1" mem="$2" sec="$3"
  shift 3
  guard
  r "docker run --rm --name ${DIR}-${name} --memory ${mem} ${sec} ${COMMON} ${TAG} python /b1/measure.py $*"
}

SECCOMP_OPT=""
step_sandbox() {
  echo "== sandbox with Docker's default seccomp profile, no capability"
  if run probe-default 1024m "" probe; then
    SECCOMP_OPT=""
    r "echo default > \$HOME/${DIR}/seccomp-choice"
    return 0
  fi
  if [[ -n "$SECCOMP" ]]; then
    echo "== sandbox with the seccomp profile $SECCOMP"
    if run probe-profile 1024m "--security-opt seccomp=\$HOME/${DIR}/seccomp.json" probe; then
      r "echo profile > \$HOME/${DIR}/seccomp-choice"
      return 0
    fi
  fi
  echo "== sandbox with seccomp unconfined (what the kernel itself allows)"
  if run probe-unconfined 1024m "--security-opt seccomp=unconfined" probe; then
    r "echo unconfined > \$HOME/${DIR}/seccomp-choice"
    return 0
  fi
  echo "PROBLEM: Chromium does not start with its sandbox on in any of the profiles"
  exit 1
}

seccomp_opt() {
  ((REAL)) || { echo ""; return 0; }
  case "$(r "cat \$HOME/${DIR}/seccomp-choice 2>/dev/null || echo default")" in
    profile) echo "--security-opt seccomp=\$HOME/${DIR}/seccomp.json" ;;
    unconfined) echo "--security-opt seccomp=unconfined" ;;
    *) echo "" ;;
  esac
}

step_matrix() { run matrix "$MEM" "$(seccomp_opt)" matrix "$PAGES" "$TABS" "$HOLD"; }
step_edge() { run edge "$MEM" "$(seccomp_opt)" edge; }
step_hog() { run hog "$HOG_MEM" "$(seccomp_opt)" hog; }

step_fetch() {
  mkdir -p "$OUT"
  if ((REAL)); then
    rsync -a --include='*.json' --include='*.jsonl' --include='*.png' --include='*.txt' --exclude='*' "${HOST}:${DIR}/out/" "$OUT/"
  else
    echo "dry: rsync ${HOST}:${DIR}/out/{*.json,*.png,*.txt} $OUT/"
  fi
}

step_clean() {
  r "docker ps -a --format '{{.Names}}' | grep '^${DIR}-' | xargs -r docker rm -f >/dev/null; \
     docker network rm ${NET} >/dev/null 2>&1 || true; docker rmi ${TAG} >/dev/null 2>&1 || true; \
     if [ -f \$HOME/${DIR}/.pulled-base ]; then docker rmi debian:bookworm-slim >/dev/null 2>&1 || true; fi; \
     rm -rf \"\$HOME/${DIR}\"; docker images | grep -c b1spike || true; free -m; df -h /"
}

echo "folder ~/${DIR}, image ${TAG}, network ${NET}, $( ((REAL)) && echo real || echo dry run)"
case "$STEP" in
  all)
    trap step_clean EXIT
    step_build
    step_sandbox
    step_matrix
    step_edge
    step_hog
    step_fetch
    ;;
  build) step_build ;;
  sandbox) step_sandbox ;;
  matrix) step_matrix ;;
  edge) step_edge ;;
  hog) step_hog ;;
  fetch) step_fetch ;;
  clean) step_clean ;;
esac
