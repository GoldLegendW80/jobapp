"""The browser container for real (task B2): its image, built from browser/, started by Compose
from the service compose.yml defines (tests/browser.compose.yml extends it), with browserd,
Xvfb and Chromium inside, driven through the control API from where the api stands (`client`).

What runs: the container's posture (uid, capabilities of browserd, read only root, memory and
swap as the kernel applies them); the secret; one lease at a time; a new profile's directory
(0700, its uid) and every Chromium process under that uid; Chromium's sandbox on
(chrome://sandbox); the AI snapshot with refs of the pinned Playwright; the languages from the
profile's preferences; the tab acted on brought to the front; a navigation timeout answered as
a failure; the egress proxy (the pages server reached, the app's client, loopback, metadata and
private addresses refused, by port and by address on ports it allows); WebRTC sending no
UDP outside the proxy (a STUN and TURN listener receives nothing); two profiles (B's Chromium
cannot read A's directory); a stale lock of another host cleared; a crash and a hang ending the
lease `browser_crashed`; the memory guard on the working set (page cache alone closes no tab,
allocated memory does); processes left under a profile uid killed; idle timeout; no lease
across a restart; SIGTERM within 10 s.

The test path into Chromium is browserd.probe, run with `docker exec` inside the container: CDP
never leaves it. Marked `docker`; the image is built under a tag of its own unless
BROWSER_TEST_IMAGE names one (a first build downloads Chromium, a few minutes).
"""

import contextlib
import json
import os
import secrets
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.pg import docker_available

ROOT = Path(__file__).resolve().parents[3]
# 8080, a port of the proxy's set: the allowed test name passes the port check too (#281).
PAGES = "http://pages:8080"
CLIENT_CALL = """
import json, sys, urllib.request, urllib.error
method, path, secret, body = sys.argv[1:5]
req = urllib.request.Request("http://browser:9300" + path, method=method,
    data=body.encode() if body else None, headers={"Content-Type": "application/json"})
if secret:
    req.add_header("Authorization", "Bearer " + secret)
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        status, data = r.status, r.read()
except urllib.error.HTTPError as e:
    status, data = e.code, e.read()
print(json.dumps({"status": status, "body": json.loads(data) if data else None}))
"""

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not (ROOT / "compose.yml").is_file(), reason="no repository root here"),
]


class Stack:
    def __init__(self, project: str, image: str) -> None:
        self.secret = secrets.token_hex(32)
        self.compose = [
            "docker",
            "compose",
            "-p",
            project,
            "--project-directory",
            str(ROOT),
            "-f",
            "tests/browser.compose.yml",
            "--profile",
            "browser",
        ]
        self.env = {
            **os.environ,
            "BROWSER_TEST_IMAGE": image,
            "BROWSER_CONTROL_SECRET": self.secret,
            "POSTGRES_USER": "unused",
            "POSTGRES_PASSWORD": "unused",
            "POSTGRES_DB": "unused",
        }

    def run(self, *arguments: str, check: bool = True, env: dict | None = None) -> str:
        done = subprocess.run(
            [*self.compose, *arguments],
            env={**self.env, **(env or {})},
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        if check and done.returncode != 0:
            raise AssertionError(f"compose {arguments[:3]}:\n{done.stdout}\n{done.stderr[-3000:]}")
        return done.stdout

    def container(self, service: str) -> str:
        return self.run("ps", "-aq", service).strip()

    def execute(self, service: str, *command: str, user: str = "", check: bool = True) -> str:
        done = subprocess.run(
            ["docker", "exec", *(["-u", user] if user else []), self.container(service), *command],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        if check:
            assert done.returncode == 0, done.stderr[-2000:]
        return done.stdout if check else f"{done.returncode}:{done.stdout}{done.stderr}"

    def call(self, method: str, path: str, body: Any = None, secret: str | None = None) -> dict:
        given = self.secret if secret is None else secret
        raw = json.dumps(body) if body is not None else ""
        out = self.execute("client", "python", "-I", "-c", CLIENT_CALL, method, path, given, raw)
        return json.loads(out)

    def probe(self, *arguments: str) -> dict:
        return json.loads(self.execute("browser", "python", "-m", "browserd.probe", *arguments))

    def python(self, code: str, *args: str, user: str = "") -> str:
        return self.execute("browser", "python", "-c", code, *args, user=user)

    def lease(self, profile: str, uid: int, purpose: str = "apply_fill") -> dict:
        body = {
            "user_id": f"u{uid}",
            "profile_id": profile,
            "uid": uid,
            "purpose": purpose,
            "languages": ["fr-FR", "fr", "en-US", "en"],
            "timezone": "Europe/Paris",
        }
        answer = self.call("POST", "/leases", body)
        assert answer["status"] == 201, answer
        return answer["body"]

    def current(self) -> dict:
        answer = self.call("GET", "/leases/current")
        assert answer["status"] == 200, answer
        return answer["body"]

    def end(self) -> None:
        lease = self.current()["lease"]
        if lease:
            # 200 with the end and the profile's size since browser B7.
            assert self.call("DELETE", f"/leases/{lease['lease_id']}")["status"] == 200

    def release_left(self) -> None:
        """Ends whatever lease a test left, whatever its outcome (no_lease_left): a test that
        fails while it holds one would fail every later test with 409 `busy` (issue #488). A
        browser that is down holds no lease, so a failing call here is not a second failure."""
        with contextlib.suppress(AssertionError, subprocess.SubprocessError, ValueError):
            self.end()

    def wait_ended(self, attempts: int = 120) -> dict:
        """Polls GET /leases/current (each call is a round trip through docker exec, no sleep)
        until the lease is gone."""
        for _ in range(attempts):
            body = self.current()
            if body["lease"] is None:
                return body
        raise AssertionError("the lease did not end")


# The real uid, as browserd reads it.
PS = """
import os, sys
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        uid = [l for l in open(f"/proc/{pid}/status") if l.startswith("Uid:")][0].split()[1]
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\\0")[0].decode()
    except OSError:
        continue
    print(uid, pid, cmd)
"""


def processes(stack: Stack) -> list[tuple[int, int, str]]:
    rows = [line.split(" ", 2) for line in stack.python(PS).splitlines()]
    return [(int(uid), int(pid), cmd) for uid, pid, cmd in rows]


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    if not docker_available():
        pytest.skip("Docker is not available")
    image = os.environ.get("BROWSER_TEST_IMAGE", "")
    built = not image
    if built:
        image = f"jobapp-browser:test-{os.getpid()}"
        done = subprocess.run(
            ["docker", "build", "-q", "-t", image, str(ROOT / "browser")],
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
        )
        assert done.returncode == 0, done.stderr[-3000:]
    s = Stack(f"browser-b2-{uuid.uuid4().hex[:8]}", image)
    try:
        s.run("up", "-d", "--wait", "--wait-timeout", "120", "--no-build")
        yield s
    finally:
        s.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)
        if built:
            subprocess.run(["docker", "rmi", "-f", image], capture_output=True, check=False)


@pytest.fixture(autouse=True)
def no_lease_left(request: pytest.FixtureRequest, stack: Stack) -> Iterator[None]:
    """After every test, passed or failed, its lease is ended: one failure stays one failure
    (issue #488). The container test files that borrow this stack import it too. Only the
    tests of one shared lease (`sweep_of_a`) keep theirs for the next."""
    yield
    if "sweep_of_a" not in request.fixturenames:
        stack.release_left()


A = str(uuid.uuid4())
B = str(uuid.uuid4())


@pytest.fixture
def sweep_of_a(stack: Stack) -> dict:
    """The sweep lease of A that the tests from test_a_lease_for_a_new_profile_and_a_second_lease
    _refused to test_the_client_reaches_the_control_api_but_chromium_reaches_only_pages share:
    the first takes it, the next ones find it, and one run after a failure ended it takes it
    again, so a failure there does not fail the rest of the chain."""
    lease = stack.current()["lease"]
    if lease is not None and (lease["profile_id"], lease["purpose"]) == (A, "sweep"):
        return lease
    stack.release_left()
    return stack.lease(A, 20001, "sweep")


def test_the_container_as_compose_starts_it(stack: Stack) -> None:
    info = json.loads(
        subprocess.run(
            ["docker", "inspect", stack.container("browser")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )[0]
    host = info["HostConfig"]
    assert info["Config"]["User"] == "0:0"
    assert host["ReadonlyRootfs"] is True
    assert host["CapDrop"] == ["ALL"]
    assert sorted(c.removeprefix("CAP_") for c in host["CapAdd"]) == ["SETGID", "SETUID"]
    assert host["Memory"] == host["MemorySwap"] == 1536 * 1024 * 1024
    assert host["ShmSize"] == 128 * 1024 * 1024
    assert host["PidsLimit"] == 1024
    assert not host.get("PortBindings")
    assert any(o.startswith("seccomp=") and '"clone"' in o for o in host["SecurityOpt"])
    names = {e.split("=", 1)[0] for e in info["Config"]["Env"]}
    assert not names & {"SECRET_KEY", "CREDENTIALS_KEY", "DATABASE_URL", "REDIS_URL"}
    # Item 6, read from the kernel: no swap beyond the memory limit.
    limits = stack.python(
        "print(open('/sys/fs/cgroup/memory.max').read().strip(),"
        " open('/sys/fs/cgroup/memory.swap.max').read().strip())"
    ).split()
    assert limits == [str(1536 * 1024 * 1024), "0"]
    # browserd's effective capabilities: SETGID (6) and SETUID (7), nothing else.
    browserd = [pid for uid, pid, cmd in processes(stack) if uid == 0 and cmd == "python"]
    status = stack.python(f"print(open('/proc/{browserd[0]}/status').read())")
    cap_eff = next(line.split()[1] for line in status.splitlines() if line.startswith("CapEff"))
    assert int(cap_eff, 16) == (1 << 6) | (1 << 7)
    assert "NoNewPrivs:\t1" in status
    xvfb = [uid for uid, _, cmd in processes(stack) if cmd == "Xvfb"]
    assert xvfb == [1000]


def test_the_secret_missing_or_wrong_starts_nothing(stack: Stack) -> None:
    body = {"user_id": "a", "profile_id": A, "uid": 20001, "purpose": "sweep"}
    # One hex digit off, always: `secret[:-1] + "0"` was the secret itself when its last digit
    # was 0 (one run in 16), which leased a sweep and made the next test find it (issue #293).
    wrong = stack.secret[:-1] + format((int(stack.secret[-1], 16) + 1) % 16, "x")
    assert wrong != stack.secret
    assert stack.call("POST", "/leases", body, secret="")["status"] == 401
    assert stack.call("POST", "/leases", body, secret=wrong)["status"] == 401
    assert stack.call("GET", "/healthz", secret="")["status"] == 401
    assert stack.current()["lease"] is None
    assert stack.call("GET", "/healthz")["body"]["ok"] is True


def test_a_lease_for_a_new_profile_and_a_second_lease_refused(
    stack: Stack, sweep_of_a: dict
) -> None:
    lease = sweep_of_a
    assert lease["lease_token"]
    assert lease["purpose"] == "sweep"
    assert lease["max_s"] == 900
    mode = stack.execute("browser", "stat", "-c", "%a %u", f"/profiles/{A}").split()
    assert mode == ["700", "20001"]
    chromium = [uid for uid, _, cmd in processes(stack) if "chromium" in cmd]
    assert chromium
    assert set(chromium) == {20001}
    second = stack.call(
        "POST", "/leases", {"user_id": "b", "profile_id": B, "uid": 20002, "purpose": "takeover"}
    )
    assert second == {"status": 409, "body": {"error": "busy", "holder_purpose": "sweep"}}
    current = stack.current()["lease"]
    assert current["lease_id"] == lease["lease_id"]
    assert "lease_token" not in current


@pytest.mark.usefixtures("sweep_of_a")
def test_chromium_sandbox_is_on(stack: Stack) -> None:
    text = stack.probe("sandbox")["text"]
    assert "You are adequately sandboxed." in text, text[:800]


@pytest.mark.usefixtures("sweep_of_a")
def test_the_ai_snapshot_with_refs_of_the_pinned_playwright(stack: Stack) -> None:
    """Item 2: fails loudly when Playwright drops snapshotForAI or its refs."""
    version = stack.python("import importlib.metadata as m; print(m.version('playwright'))")
    assert version.strip() == "1.55.0"
    snapshot = stack.probe("snapshot", f"{PAGES}/form.html")["snapshot"]
    assert "[ref=e" in snapshot, snapshot[:600]
    assert 'textbox "First name *" [ref=e' in snapshot, snapshot[:600]


@pytest.mark.usefixtures("sweep_of_a")
def test_languages_zone_user_agent_and_webgl(stack: Stack) -> None:
    """Items 3, 4 and 7: the languages from the preferences, Chromium's own user agent, no
    WebGL under Xvfb."""
    env = stack.probe("env", f"{PAGES}/headers")
    assert env["languages"] == ["fr-FR", "fr", "en-US", "en"]
    assert env["zone"] == "Europe/Paris"
    assert "Linux" in env["userAgent"]
    assert "Headless" not in env["userAgent"]
    assert env["webdriver"] is False
    assert env["webgl"] is False
    assert env["accept_language"].startswith("fr-FR,fr;q=0.9")


@pytest.mark.usefixtures("sweep_of_a")
def test_the_tab_acted_on_comes_to_the_front(stack: Stack) -> None:
    """Item 8. Under Xvfb with no window manager Chromium computes no occlusion, so the tab in
    the background still reads "visible"; what is proved is that the tab acted on holds the
    focus after pages.front (B1 measured the throttling it avoids: 5.9 s against 0.9 s)."""
    assert stack.probe("front", f"{PAGES}/form.html") == {"visibility": "visible", "focus": True}


@pytest.mark.usefixtures("sweep_of_a")
def test_a_navigation_timeout_is_a_failure_result(stack: Stack) -> None:
    result = stack.probe("navigate", f"{PAGES}/slow.html", "1")
    assert result["ok"] is False
    assert result["error"] == "navigation_timeout"
    assert stack.probe("navigate", f"{PAGES}/form.html", "20")["ok"] is True


@pytest.mark.parametrize(
    "url",
    [
        # Refused for the port alone, the allowed test name included (issue #281).
        "http://127.0.0.1:9300/healthz",
        "http://pages-ip:8000/",
        "http://pages:8000/",
        "http://pages:3478/",
        # Refused for the address: every port here is one the proxy allows, and an answer of
        # 403 (not 502, not a page) is the address rule's own.
        "http://client:8080/",
        "http://pages-ip:8080/form.html",
        "http://127.0.0.1:8080/",
        "http://localhost:8080/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://100.100.100.100/",
        "http://192.168.1.1/",
        "https://172.17.0.1/",
        "https://pages-ip:8443/",
    ],
)
@pytest.mark.usefixtures("sweep_of_a")
def test_the_egress_proxy_refuses_what_is_not_public(stack: Stack, url: str) -> None:
    url = url.replace("pages-ip", pages_ip(stack))
    result = stack.probe("navigate", url, "20")
    if url.startswith("http://"):
        assert result.get("status") == 403, result
    else:  # a refused CONNECT: Chromium shows its own tunnel error
        assert result.get("error") == "navigation_failed", result
    assert "Directory listing" not in result.get("text", "")
    assert "browserd" not in result.get("text", "")
    assert "First name" not in result.get("text", "")


@pytest.mark.usefixtures("sweep_of_a")
def test_the_pages_server_answers_on_8080_by_its_allowed_name(stack: Stack) -> None:
    """The control of the address cases above: the same port answers by the allowed name."""
    assert stack.probe("navigate", "http://pages:8080/form.html", "20")["status"] == 200


@pytest.mark.usefixtures("sweep_of_a")
def test_the_upstream_reads_the_host_the_proxy_checked(stack: Stack) -> None:
    """Issue #281 item 6: the Host header the pages server reads is the checked authority, and
    no proxy header reaches it."""
    result = stack.probe("navigate", f"{PAGES}/headers", "20")
    assert result["status"] == 200, result
    echoed = json.loads(result["text"])
    assert echoed["host"] == "pages:8080"
    assert echoed["connection"] == "close"
    assert "proxy-connection" not in echoed
    assert "keep-alive" not in echoed


def pages_ip(stack: Stack) -> str:
    return stack.python("import socket; print(socket.gethostbyname('pages'))").strip()


def datagrams(stack: Stack) -> int:
    code = "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/udp').read().decode())"
    return json.loads(stack.execute("pages", "python", "-I", "-c", code))["count"]


@pytest.mark.usefixtures("sweep_of_a")
def test_webrtc_sends_no_udp_outside_the_proxy(stack: Stack) -> None:
    """Review of PR #267, blocking 1: a page's RTCPeerConnection with STUN and TURN servers over
    UDP, by the pages server's private address and by its name. Not one datagram reaches the
    listener (the managed policy and the profile's preference, disable_non_proxied_udp), no UDP
    candidate is gathered, and Chromium resolves no name itself (--host-resolver-rules), so the
    STUN name causes no DNS query outside the proxy. Then the control: a datagram the container
    itself sends to the same listener arrives, so the route and the count are real."""
    address = pages_ip(stack)
    servers = [
        {"urls": f"stun:{address}:3478"},
        {"urls": "stun:pages:3478"},
        {"urls": f"turn:{address}:3478?transport=udp", "username": "u", "credential": "p"},
        {"urls": "turn:pages:3478?transport=udp", "username": "u", "credential": "p"},
    ]
    result = stack.probe("webrtc", f"{PAGES}/form.html", json.dumps(servers))
    assert result["complete"] is True, result
    assert not [c for c in result["candidates"] if " udp " in c.lower()], result
    assert datagrams(stack) == 0
    # No WebRTC mDNS responder (issue #281): no socket on 5353 (hex 14E9) and no membership of
    # 224.0.0.251 (FB0000E0 in /proc/net/igmp) in the container's network namespace.
    sockets = stack.execute("browser", "cat", "/proc/net/udp", "/proc/net/udp6")
    assert ":14E9 " not in sockets, sockets
    assert "FB0000E0" not in stack.execute("browser", "cat", "/proc/net/igmp")
    policy = stack.probe("policy")
    assert all(policy.values()), policy
    send = "import socket, sys; socket.socket(2, 2).sendto(b'x', (sys.argv[1], 3478))"
    stack.python(send, address, user="1000")
    for _ in range(50):  # each read is a docker exec round trip, no sleep
        if datagrams(stack):
            break
    assert datagrams(stack) == 1


@pytest.mark.usefixtures("sweep_of_a")
def test_the_client_reaches_the_control_api_but_chromium_reaches_only_pages(stack: Stack) -> None:
    assert stack.probe("navigate", f"{PAGES}/form.html", "20")["status"] == 200
    reach = stack.execute(
        "client",
        "python",
        "-I",
        "-c",
        "import socket; socket.create_connection(('pages', 8000), 3)",
        check=False,
    )
    assert not reach.startswith("0:"), "the client is on `browsing` alone"


def test_two_profiles_b_cannot_read_a(stack: Stack) -> None:
    stack.end()
    stack.lease(B, 20002, "apply_fill")
    seen = stack.probe("navigate", f"file:///profiles/{A}/data/", "10")
    assert "Default" not in seen.get("text", ""), seen
    denied = stack.execute("browser", "ls", f"/profiles/{A}", user="20002", check=False)
    assert not denied.startswith("0:")
    assert "Permission denied" in denied
    listing = stack.execute("browser", "ls", "/profiles", user="20002", check=False)
    assert "Permission denied" in listing
    chromium = {uid for uid, _, cmd in processes(stack) if "chromium" in cmd}
    assert chromium == {20002}
    stack.end()
    assert not [p for p in processes(stack) if p[0] == 20002]


def test_one_uid_never_serves_a_second_profile(stack: Stack) -> None:
    """Issue #281 item 8: 20001 holds A, so a lease for a new profile under 20001 is refused
    409 and creates no directory; browserd's record is root's own, 0700."""
    stack.end()
    other = str(uuid.uuid4())
    body = {"user_id": "c", "profile_id": other, "uid": 20001, "purpose": "apply_fill"}
    answer = stack.call("POST", "/leases", body)
    assert answer["status"] == 409, answer
    assert answer["body"]["error"] == "uid_in_use"
    assert stack.current()["lease"] is None
    listing = stack.execute("browser", "ls", "-a", "/profiles")
    assert other not in listing
    record = stack.execute("browser", "stat", "-c", "%u %a", "/profiles/.uids").split()
    assert record == ["0", "700"]
    assert stack.execute("browser", "cat", "/profiles/.uids/20001") == A


def test_a_stale_lock_of_another_host_is_cleared(stack: Stack) -> None:
    stack.execute(
        "browser", "ln", "-sfn", "otherhost-4242", f"/profiles/{A}/data/SingletonLock", user="20001"
    )
    stack.lease(A, 20001)
    target = stack.execute(
        "browser", "readlink", f"/profiles/{A}/data/SingletonLock", user="20001", check=False
    )
    assert "otherhost-4242" not in target
    stack.end()


# A new file of the profiles volume (ext4, not a tmpfs) written until memory.current reaches 95%
# of the limit: page cache charged to the container, which the kernel reclaims on demand.
FILL_PAGE_CACHE = """
import json, os, sys, time
from browserd import chromium
def current():
    with open('/sys/fs/cgroup/memory.current') as f:
        return int(f.read())
with open('/sys/fs/cgroup/memory.max') as f:
    limit = int(f.read())
chunk, written = bytes(1 << 20), 0
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    while current() * 100 < limit * 95 and written < 2 * limit:
        written += os.write(fd, chunk)
    os.fsync(fd)
finally:
    os.close(fd)
print(json.dumps({"current": current(), "limit": limit, "working_set": chromium.memory()[0],
                  "at": time.monotonic()}))
"""
READ_MEMORY = """
import json, time
from browserd import chromium
with open('/sys/fs/cgroup/memory.current') as f:
    print(json.dumps({"current": int(f.read()), "at": time.monotonic()}))
"""
# Memory allocated and touched up to argv[1] % of the limit by the working set, held until the
# test closes its stdin.
HOLD = """
import json, sys
from browserd import chromium
used, limit = chromium.memory()
held = bytearray(max(0, limit * int(sys.argv[1]) // 100 - used))
held[::4096] = b"\\1" * len(range(0, len(held), 4096))
print(json.dumps({"held": len(held), "working_set": chromium.memory()[0]}), flush=True)
sys.stdin.read()
"""


def memory_events(stack: Stack) -> list[dict]:
    lease = stack.current()["lease"]
    assert lease is not None, "the lease ended"
    return [e for e in lease["events"] if e.get("reason") == "memory"]


def test_page_cache_alone_never_trips_the_memory_guard_and_allocation_does(stack: Stack) -> None:
    """Page cache past 92% of the limit by memory.current closes no tab over three rounds of the
    guard (every 2 s, Chromium._watch, counted on the container's clock); memory a process
    allocates past 90% of the working set closes one."""
    stack.lease(A, 20001)
    cache = f"/profiles/.page-cache-{secrets.token_hex(4)}"
    try:
        filled = json.loads(stack.python(FILL_PAGE_CACHE, cache))
        limit = filled["limit"]
        # The fill stops at 95%, and the fsync after it lets the kernel take some back (94.9%
        # was read, issue #104): past the guard's 90% with a margin is what this needs.
        assert filled["current"] * 100 >= limit * 92
        assert filled["working_set"] * 100 < limit * 60
        for _ in range(400):
            assert memory_events(stack) == []
            now = json.loads(stack.python(READ_MEMORY))
            if now["at"] - filled["at"] >= 7:
                break
        else:
            raise AssertionError("three rounds of the guard did not pass")
        # Still past 90% by memory.current: the figure read before would have closed a tab.
        assert now["current"] * 100 >= limit * 90
        assert memory_events(stack) == []
        hold = ["docker", "exec", "-i", stack.container("browser"), "python", "-c", HOLD, "92"]
        with subprocess.Popen(
            hold, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        ) as holder:
            try:
                held = json.loads(holder.stdout.readline())
                assert held["working_set"] * 100 >= limit * 90, held
                for _ in range(400):
                    events = memory_events(stack)
                    if events:
                        break
            finally:
                holder.stdin.close()
                holder.wait(timeout=60)
        assert [(e["event"], e["reason"]) for e in events] == [("tab_closed", "memory")]
        assert events[0]["used_mib"] * 100 >= (limit >> 20) * 90
    finally:
        stack.execute("browser", "rm", "-f", cache)
        stack.end()


def test_a_crash_ends_the_lease_browser_crashed(stack: Stack) -> None:
    stack.lease(A, 20001)
    kill = (
        "import os\nfor p in filter(str.isdigit, os.listdir('/proc')):\n"
        " if int(p) != os.getpid():\n  try: os.kill(int(p), 9)\n  except OSError: pass"
    )
    stack.python(kill, user="20001")
    ended = stack.wait_ended()
    assert ended["last_end"]["reason"] == "browser_crashed"
    assert not [p for p in processes(stack) if p[0] == 20001]


def test_a_hang_ends_the_lease_browser_crashed(stack: Stack) -> None:
    stack.lease(A, 20001)
    main = min(pid for uid, pid, cmd in processes(stack) if uid == 20001 and "chromium" in cmd)
    stack.python(f"import os; os.kill({main}, 19)", user="20001")  # SIGSTOP: no CDP answer
    ended = stack.wait_ended(attempts=400)
    assert ended["last_end"]["reason"] == "browser_crashed"
    assert not [p for p in processes(stack) if p[0] == 20001]


def test_processes_left_under_a_profile_uid_are_killed(stack: Stack) -> None:
    subprocess.run(
        [
            "docker",
            "exec",
            "-d",
            "-u",
            "20007",
            stack.container("browser"),
            "python",
            "-c",
            "import time; time.sleep(600)",
        ],
        check=True,
    )
    assert [p for p in processes(stack) if p[0] == 20007]
    killed = stack.python(
        "import asyncio; from browserd.chromium import reap_strays;"
        " print(asyncio.run(reap_strays()))"
    )
    assert int(killed) >= 1
    assert not [p for p in processes(stack) if p[0] == 20007]
    main = (ROOT / "browser/browserd/__main__.py").read_text(encoding="utf-8")
    assert main.index("reap_strays()") < main.index("start_display()")


# A process that never keeps its pid: each one forks and exits, so a list of pids read before the
# kill names only processes already gone.
FORKER = """
import os, time
while True:
    time.sleep(0.005)
    if os.fork():
        os._exit(0)
"""


def test_a_process_that_keeps_forking_is_reaped(stack: Stack) -> None:
    """Review of PR #429: the reap killed a list of pids, which a forking process outran, so every
    start was refused until browserd restarted. Now one kill(-1) sent as the uid."""
    browser = stack.container("browser")
    subprocess.run(
        ["docker", "exec", "-d", "-u", "20008", browser, "python", "-c", FORKER], check=True
    )
    for _ in range(100):
        if [p for p in processes(stack) if p[0] == 20008]:
            break
    else:
        raise AssertionError("the forking process did not start")
    stack.python(
        "import asyncio; from browserd.chromium import clear_display; asyncio.run(clear_display())"
    )
    assert not [p for p in processes(stack) if p[0] == 20008]
    # The kill(-1) is refused under any uid outside the profile range (here the display's).
    refused = stack.execute(
        "browser", "python", "-m", "browserd.prep", "killuid", user="1000", check=False
    )
    assert not refused.startswith("0:"), refused
    assert [p for p in processes(stack) if p[0] == 1000]


def test_idle_timeout_and_no_lease_across_a_restart(stack: Stack) -> None:
    stack.lease(A, 20001)
    stack.run("up", "-d", "--wait", "--no-build", "browser", env={"BROWSER_IDLE_S": "2"})
    assert stack.current() == {"lease": None, "last_end": None}
    stack.lease(A, 20001)
    assert stack.wait_ended(attempts=400)["last_end"]["reason"] == "idle"


def test_sigterm_closes_chromium_cleanly_within_10_s(stack: Stack) -> None:
    stack.lease(A, 20001)
    stack.run("stop", "-t", "10", "browser")
    info = json.loads(
        subprocess.run(
            ["docker", "inspect", stack.container("browser")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )[0]
    assert info["State"]["ExitCode"] == 0, info["State"]
    logs = stack.run("logs", "--no-color", "browser")
    assert "chromium closed" in logs
