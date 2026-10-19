"""Browser B7 in the real container (the stack of test_browser_container.py, its own project):
the persisted profile and a person's own session without a debugging port.

What runs: a sign in session and a look session have no listening port and no debugging switch
(an agent's lease of the same uid does, the control); the typed address reaches Chromium's own
binary after `--`, and a switch written after `--` opens nothing, through Debian's wrapper;
browserd refuses a bad address and starts nothing; cookies and local storage of a sign in
session (a cookie without an expiry date among them) are in the next agent's lease and after the
container is recreated; SIGTERM to browserd during a session without CDP closes Chromium within
10 s and keeps that session's cookies; a Chromium without CDP killed from outside ends the lease
`browser_crashed` with the profile intact, and one whose window closes ends it
`browser_closed`; the memory guard ends a session without CDP `memory_guard`; a profile of
1.2 GB is trimmed under 1 GB at the lease end without losing its cookies, and the end answers the
new size.

Hardening (issue #424): an end whose helper raises leaves no browser on the display for the next
lease (the end reaps, and every start reaps what an end could not); chrome://inspect is blocked by
the managed policy, a remote debugging switch kept on in the profile's Local State opens no port
in a person's session, and a port opened in one ends it `debugging_port`.

The fixture site is tests/browser_pages/b7/site.html: its title says who is signed in, which the
test reads from the display's window list (xwininfo), since nothing may attach to a session
without CDP. Marked `docker`, like the module it borrows the stack from.
"""

import json
import subprocess
import uuid

import pytest

from tests.browser.test_browser_container import PAGES, Stack, processes, stack

__all__ = ["stack"]

pytestmark = [pytest.mark.docker]

SITE = f"{PAGES}/b7/site.html"
A, UID_A = str(uuid.uuid4()), 20011
B, UID_B = str(uuid.uuid4()), 20012
C, UID_C = str(uuid.uuid4()), 20013
D, UID_D = str(uuid.uuid4()), 20021
E, UID_E = str(uuid.uuid4()), 20022
F, UID_F = str(uuid.uuid4()), 20023
G, UID_G = str(uuid.uuid4()), 20024
GB = 1 << 30

LISTENING = """
import sys
for name in ("/proc/net/tcp", "/proc/net/tcp6"):
    for line in open(name).read().splitlines()[1:]:
        f = line.split()
        if f[3] == "0A" and f[7] == sys.argv[1]:
            print(int(f[1].rsplit(":", 1)[1], 16))
"""

ARGV = """
import sys
for pid in sys.argv[2:]:
    try:
        raw = open(f"/proc/{pid}/cmdline", "rb").read()
    except OSError:
        continue
    parts = [p.decode() for p in raw.split(b"\\0") if p]
    if len(parts) == 1:
        parts = parts[0].split(" ")
    if not any(p.startswith("--type=") for p in parts) and any("user-data-dir" in p for p in parts):
        print("\\0".join(parts))
"""


def session(stack: Stack, purpose: str, profile: str, uid: int, address: str | None = None) -> dict:
    body = {"user_id": f"u{uid}", "profile_id": profile, "uid": uid, "purpose": purpose,
            "automation": False}  # fmt: skip
    if address is not None:
        body["address"] = address
    answer = stack.call("POST", "/leases", body)
    assert answer["status"] == 201, answer
    assert answer["body"]["automation"] is False
    return answer["body"]


def listening(stack: Stack, uid: int) -> list[int]:
    return [int(p) for p in stack.python(LISTENING, str(uid)).split()]


def browser_argv(stack: Stack, uid: int) -> list[str]:
    pids = [str(pid) for owner, pid, _ in processes(stack) if owner == uid]
    out = stack.python(ARGV, "x", *pids).strip()
    assert out, "no Chromium browser process"
    return out.splitlines()[0].split("\0")


def window_titles(stack: Stack) -> str:
    return stack.execute("browser", "xwininfo", "-display", ":1", "-root", "-tree", user="1000")


def wait_title(stack: Stack, text: str, attempts: int = 300) -> None:
    """Each look is a docker exec round trip, no sleep."""
    for _ in range(attempts):
        if text in window_titles(stack):
            return
    raise AssertionError(f"no window titled {text!r}")


def end(stack: Stack) -> dict:
    lease = stack.current()["lease"]
    assert lease is not None
    answer = stack.call("DELETE", f"/leases/{lease['lease_id']}")
    assert answer["status"] == 200, answer
    return answer["body"]


def read_site(stack: Stack, profile: str, uid: int) -> str:
    """An agent's lease on the profile reads the site through CDP (browserd.probe)."""
    stack.lease(profile, uid)
    seen = stack.probe("navigate", SITE, "20")
    end(stack)
    return str(seen.get("text", ""))


def sign_in(stack: Stack, profile: str, uid: int, name: str) -> None:
    session(stack, "signin", profile, uid, f"{SITE}?login={name}")
    wait_title(stack, f"signed in as {name}")


def test_person_sessions_have_no_debugging_port(stack: Stack) -> None:
    stack.lease(A, UID_A)  # the control: an agent's lease of that uid listens
    assert listening(stack, UID_A)
    end(stack)
    for purpose in ("signin", "takeover"):
        session(stack, purpose, A, UID_A)
        assert stack.current()["lease"]["purpose"] == purpose
        assert listening(stack, UID_A) == []
        argv = browser_argv(stack, UID_A)
        assert not [a for a in argv if "remote-debugging" in a or "AutomationControlled" in a]
        assert not [cmd for owner, _, cmd in processes(stack) if owner == UID_A and "debug" in cmd]
        end(stack)


def test_the_typed_address_comes_after_the_terminator(stack: Stack) -> None:
    address = f"{SITE}?login=e2e-browser-a&next=/a"
    session(stack, "signin", A, UID_A, address)
    argv = browser_argv(stack, UID_A)
    assert argv[-2:] == ["--", address]
    assert argv.count("--") == 1  # the wrapper's own terminator was taken by the wrapper
    wait_title(stack, "signed in as e2e-browser-a")
    end(stack)


SWITCH_AFTER = """
import os, subprocess, sys, uuid
from browserd import chromium
from browserd.leases import Request
profile = sys.argv[1]
r = Request.parse({"user_id": "t", "profile_id": profile, "uid": 20019, "purpose": "apply_fill"})
os.makedirs(f"/profiles/{profile}/data", mode=0o700)
argv = chromium.switches(r, "/tmp", 9556, "--remote-debugging-port=9555")
p = subprocess.Popen(argv, env=chromium.child_env(r), start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(p.pid)
"""


def test_a_switch_after_the_terminator_is_never_read_as_one(stack: Stack) -> None:
    """Even past the address check: Chromium started by browserd's own switches with the
    address `--remote-debugging-port=9555` listens on the port it was given (9556, the
    control) and never on 9555, through Debian's wrapper script."""
    assert stack.current()["lease"] is None
    profile = str(uuid.uuid4())
    pid = stack.python(SWITCH_AFTER, profile, user="20019").strip()
    try:
        ports: list[int] = []
        for _ in range(300):  # docker exec round trips, no sleep
            ports = listening(stack, 20019)
            if 9556 in ports:
                break
        assert 9556 in ports
        assert 9555 not in ports
    finally:
        stack.python(f"import os, signal; os.killpg({pid}, signal.SIGKILL)", user="20019")
        stack.execute("browser", "rm", "-rf", f"/profiles/{profile}", user="20019")


@pytest.mark.parametrize(
    "address",
    ["--remote-debugging-port=9222", "-a", "javascript:alert(1)", "file:///etc/passwd",
     "chrome://settings", "data:text/html,x", "https://", "https://" + "a" * 5000],
)  # fmt: skip
def test_browserd_refuses_a_bad_address_and_starts_nothing(stack: Stack, address: str) -> None:
    body = {"user_id": "u", "profile_id": A, "uid": UID_A, "purpose": "signin",
            "automation": False, "address": address}  # fmt: skip
    answer = stack.call("POST", "/leases", body)
    assert (answer["status"], answer["body"]) == (400, {"error": "bad_address"})
    assert stack.current()["lease"] is None
    assert not [p for p in processes(stack) if p[0] == UID_A]


def test_a_signin_sessions_cookies_are_in_the_next_lease_and_after_a_recreate(
    stack: Stack,
) -> None:
    sign_in(stack, A, UID_A, "e2e-browser-a")
    end(stack)
    text = read_site(stack, A, UID_A)
    assert "Signed in as e2e-browser-a" in text
    assert "session cookie: yes, lasting cookie: yes, storage: yes" in text
    stack.run("up", "-d", "--force-recreate", "--wait", "--no-build", "browser")
    text = read_site(stack, A, UID_A)
    assert "session cookie: yes, lasting cookie: yes, storage: yes" in text


def test_sigterm_to_browserd_closes_a_session_without_cdp_and_keeps_its_cookies(
    stack: Stack,
) -> None:
    sign_in(stack, B, UID_B, "e2e-browser-b")
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
    assert "chromium closed" in stack.run("logs", "--no-color", "browser")
    stack.run("up", "-d", "--wait", "--no-build", "browser")
    text = read_site(stack, B, UID_B)
    assert "Signed in as e2e-browser-b" in text
    assert "session cookie: yes" in text


def test_a_session_without_cdp_killed_from_outside_ends_browser_crashed(stack: Stack) -> None:
    sign_in(stack, C, UID_C, "e2e-browser-c")
    end(stack)
    session(stack, "takeover", C, UID_C)
    kill = (
        "import os\nfor p in filter(str.isdigit, os.listdir('/proc')):\n"
        " if int(p) != os.getpid():\n  try: os.kill(int(p), 9)\n  except OSError: pass"
    )
    stack.python(kill, user=str(UID_C))
    ended = stack.wait_ended()
    assert ended["last_end"]["reason"] == "browser_crashed"
    assert not [p for p in processes(stack) if p[0] == UID_C]
    assert "Signed in as e2e-browser-c" in read_site(stack, C, UID_C)


def test_the_memory_guard_ends_a_session_without_cdp(stack: Stack) -> None:
    stack.run("up", "-d", "--wait", "--no-build", "browser", env={"BROWSER_MEM_GUARD_PCT": "50"})
    try:
        session(stack, "signin", C, UID_C, f"{PAGES}/hog.html?cap=400")
        ended = stack.wait_ended(attempts=600)
        assert ended["last_end"]["reason"] == "memory_guard"
        assert not [p for p in processes(stack) if p[0] == UID_C]
    finally:
        stack.run("up", "-d", "--wait", "--no-build", "browser")


def test_a_profile_over_1_gb_is_trimmed_and_its_size_answered(stack: Stack) -> None:
    sign_in(stack, A, UID_A, "e2e-browser-a")
    end(stack)
    cache = f"/profiles/{A}/data/Default/Cache/Cache_Data"
    stack.execute("browser", "mkdir", "-p", cache, user=str(UID_A))
    stack.execute("browser", "truncate", "-s", "1200M", f"{cache}/b7-big", user=str(UID_A))
    stack.lease(A, UID_A)
    answer = end(stack)
    assert 0 < answer["size_bytes"] < GB
    gone = stack.execute("browser", "ls", f"{cache}/b7-big", user=str(UID_A), check=False)
    assert not gone.startswith("0:")
    assert "session cookie: yes, lasting cookie: yes" in read_site(stack, A, UID_A)


# --- hardening, issue #424 -----------------------------------------------------------------------

FAILING_END = """
import asyncio, json, subprocess, sys, uuid
from browserd import chromium, signin
from browserd import config as c
from browserd.leases import Request
profile, uid, address, end, failing = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5].split(",")
real = chromium.as_uid

async def as_uid(who, *argv, timeout=15):
    if argv[3] in failing:
        raise OSError(argv[3] + " could not start")
    return await real(who, *argv, timeout=timeout)

def titles():
    return subprocess.run(["xwininfo", "-display", ":1", "-root", "-tree"],
                          capture_output=True, text=True).stdout

async def main():
    body = {"user_id": "t", "profile_id": profile, "uid": uid, "purpose": "signin",
            "automation": False, "address": address}
    browser = signin.PersonChromium(Request.parse(body), str(uuid.uuid4()), None,
                                    c.Config.from_env())
    await browser.start()
    shown = any("signed in as" in titles() for _ in range(600))
    chromium.as_uid = as_uid
    try:
        await getattr(browser, end)()
    except OSError as exc:
        raised = str(exc)
    else:
        raised = ""
    print(json.dumps({"shown": shown, "raised": raised}))

asyncio.run(main())
"""  # noqa: E501


@pytest.mark.parametrize(
    ("end_with", "failing", "left_by_the_end"),
    [("stop", "quit", False), ("kill", "killpg", False), ("stop", "quit,kill", True)],
)
def test_an_end_whose_helper_raises_leaves_no_browser_for_the_next_lease(
    stack: Stack, end_with: str, failing: str, left_by_the_end: bool
) -> None:
    """D's session ends with its helper raising (the quit, the kill of its group, and in the last
    case the reap of the end too). Nothing of D is on the display when E's lease runs: the end
    reaped it, or E's start did."""
    assert stack.current()["lease"] is None
    address = f"{SITE}?login=e2e-browser-d"
    out = json.loads(stack.python(FAILING_END, D, str(UID_D), address, end_with, failing))
    assert out == {"shown": True, "raised": f"{failing.split(',')[0]} could not start"}
    left = [p for p in processes(stack) if p[0] == UID_D]
    assert bool(left) is left_by_the_end
    assert ("signed in as e2e-browser-d" in window_titles(stack)) is left_by_the_end
    sign_in(stack, E, UID_E, "e2e-browser-e")
    assert "signed in as e2e-browser-d" not in window_titles(stack)
    assert not [p for p in processes(stack) if p[0] == UID_D]
    end(stack)


SWITCH_ON = """
import json, sys
path = f"/profiles/{sys.argv[1]}/data/Local State"
state = json.load(open(path))
state.setdefault("devtools", {})["remote_debugging"] = {"user-enabled": True}
json.dump(state, open(path, "w"))
"""

SWITCH = """
import json, sys
state = json.load(open(f"/profiles/{sys.argv[1]}/data/Local State"))
print(json.dumps(state.get("devtools", {}).get("remote_debugging")))
"""

ALL_LISTENING = """
for name in ("/proc/net/tcp", "/proc/net/tcp6"):
    for line in open(name).read().splitlines()[1:]:
        f = line.split()
        if f[3] == "0A":
            print(int(f[1].rsplit(":", 1)[1], 16), f[7])
"""


@pytest.mark.parametrize(
    "page", ["chrome://inspect/#remote-debugging", "chrome://inspect", "CHROME://INSPECT/#devices",
             "chrome://%69nspect/", "chrome:inspect", "view-source:chrome://inspect"],
)  # fmt: skip
def test_chrome_inspect_is_blocked_by_the_managed_policy(stack: Stack, page: str) -> None:
    """Its "Allow remote debugging" switch opens a DevTools port at the next start (measured
    for B7); an agent's lease still has CDP (this navigation goes through it)."""
    stack.lease(F, UID_F)
    seen = stack.probe("navigate", page, "20")
    end(stack)
    assert seen["ok"] is False
    assert "ERR_BLOCKED_BY_ADMINISTRATOR" in seen["detail"]


def test_a_switch_kept_on_in_the_profile_opens_no_port_in_a_persons_session(
    stack: Stack,
) -> None:
    """The switch as Chromium keeps it in Local State (a profile from before the policy): prep
    turns it off before every start, so neither session listens, on 9222 or any port, from its
    page shown to well after (the watch would have ended it `debugging_port`)."""
    sign_in(stack, F, UID_F, "e2e-browser-f")
    end(stack)
    for purpose in ("signin", "takeover"):
        stack.python(SWITCH_ON, F, user=str(UID_F))
        assert json.loads(stack.python(SWITCH, F, user=str(UID_F))) == {"user-enabled": True}
        session(stack, purpose, F, UID_F, SITE)
        wait_title(stack, "signed in as e2e-browser-f")
        for _ in range(10):  # docker exec round trips, no sleep
            assert listening(stack, UID_F) == []
            assert not [line for line in stack.python(ALL_LISTENING).splitlines()
                        if line.split()[0] == "9222"]  # fmt: skip
            assert stack.current()["lease"]["purpose"] == purpose
        assert json.loads(stack.python(SWITCH, F, user=str(UID_F))) == {"user-enabled": False}
        end(stack)


def test_a_port_opened_in_a_persons_session_ends_it(stack: Stack) -> None:
    sign_in(stack, G, UID_G, "e2e-browser-g")
    listener = (
        "import socket, time\ns = socket.socket()\ns.bind(('127.0.0.1', 9222))\n"
        "s.listen()\ntime.sleep(600)"
    )
    subprocess.run(
        ["docker", "exec", "-d", "-u", str(UID_G), stack.container("browser"), "python", "-c",
         listener],
        check=True,
    )  # fmt: skip
    ended = stack.wait_ended()
    assert ended["last_end"]["reason"] == "debugging_port"
    assert not [p for p in processes(stack) if p[0] == UID_G]


def test_a_window_that_closes_ends_the_session_browser_closed(stack: Stack) -> None:
    """Chromium quitting by itself (status 0) is not a crash; a kill from outside still is
    (test_a_session_without_cdp_killed_from_outside_ends_browser_crashed)."""
    profile, uid = str(uuid.uuid4()), 20025
    session(stack, "signin", profile, uid, f"{PAGES}/b7/close.html")
    ended = stack.wait_ended(attempts=300)
    assert ended["last_end"]["reason"] == "browser_closed"
    assert not [p for p in processes(stack) if p[0] == uid]
