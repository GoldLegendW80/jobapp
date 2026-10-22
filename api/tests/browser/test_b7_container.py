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
lease (the end reaps, and every start reaps what an end could not); every chrome:// page but
chrome://policy, chrome://sandbox and the new tab page is blocked by the managed policy, in an
agent's lease and in a person's session (owner, 2026-10-20); chrome://policy/test shows
chrome://policy, and its "Apply policies" message reopens no page; a remote debugging switch kept
on in the profile's Local State opens no port in a person's session, a flag or a test policy set
saved there does not survive a restart, and a port opened in a person's session ends it
`debugging_port`. A lease opens its address alone, never the tabs the last one left (#488).

The fixture site is tests/browser_pages/b7/site.html: its title says who is signed in, which the
test reads from the display's window list (xwininfo), since nothing may attach to a session
without CDP. Marked `docker`, like the module it borrows the stack from.
"""

import contextlib
import json
import subprocess
import uuid
from collections.abc import Iterator

import pytest

from tests.browser.test_browser_container import PAGES, Stack, no_lease_left, processes, stack

__all__ = ["no_lease_left", "stack"]

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


ARGV_ALL = """
import sys
for pid in sys.argv[1:]:
    try:
        print(open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\\0", b" ").decode())
    except OSError:
        pass
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
    raise AssertionError(f"no window titled {text!r}: {window_titles(stack)[-1500:]}")


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
    [("stop", "quit", False), ("kill", "killpg", False), ("stop", "quit,killuid", True)],
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


BLOCKED = ["chrome://flags", "chrome://flags/#enable-experimental-web-platform-features",
           "chrome://settings", "chrome://settings/content", "chrome://extensions",
           "chrome://net-internals", "chrome://inspect/#remote-debugging", "chrome://inspect",
           "CHROME://INSPECT/#devices", "chrome://%69nspect/", "chrome:inspect",
           "view-source:chrome://inspect", "chrome://%66lags", "view-source:chrome://flags",
           "chrome://chrome-urls", "chrome://version"]  # fmt: skip


@pytest.mark.parametrize("page", BLOCKED)
def test_chrome_pages_are_blocked_by_the_managed_policy_in_an_agents_lease(
    stack: Stack, page: str
) -> None:
    """Every chrome:// page is blocked (owner, 2026-10-20); an agent's lease still has CDP (this
    navigation goes through it)."""
    stack.lease(F, UID_F)
    seen = stack.probe("navigate", page, "20")
    end(stack)
    assert seen["ok"] is False
    assert "ERR_BLOCKED_BY_ADMINISTRATOR" in seen["detail"]


TARGETS = """
import json, urllib.request
port = json.load(open("/tmp/browserd/current.json"))["port"]
print(json.dumps(json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list"))))
"""


def test_the_chrome_pages_the_image_needs_stay_open(stack: Stack) -> None:
    """browserd.probe reads chrome://sandbox and chrome://policy; Ctrl+T opens the new tab
    page. The browser's own UI is made of chrome://*.top-chrome pages: blocked, the omnibox
    popup became an error page among the tabs, and a tab a page opened never loaded for CDP."""
    stack.lease(F, UID_F)
    sandbox = stack.probe("sandbox")["text"]
    policy = stack.probe("policy")
    seen = stack.probe("navigate", "chrome://new-tab-page", "20")
    front = stack.probe("front", f"{PAGES}/form.html")
    targets = json.loads(stack.python(TARGETS))
    end(stack)
    assert front["visibility"] == "visible", front
    assert [t for t in targets if ".top-chrome" in t["url"]], targets
    assert not [t for t in targets if ".top-chrome" in t["url"] and t["type"] == "page"], targets
    assert not [t for t in targets if t["url"].startswith("chrome-error:")], targets
    assert "You are adequately sandboxed." in sandbox, sandbox[:800]
    assert all(policy.values()), policy
    assert seen["ok"] is True, seen


SAVED_TABS = """
import os, sys
print(len(os.listdir(f"/profiles/{sys.argv[1]}/data/Default/Sessions")))
"""


def test_a_lease_opens_its_address_alone_whatever_the_last_one_left(stack: Stack) -> None:
    """Issue #488: "continue where you left off" reopened the last session's tabs beside the new
    one's address, a tab more at every lease of the profile, until a tab a page opened went past
    TABS_MAX and was closed (the probe's second tab above, in the gate). Chromium saves the tabs
    at every end; prep removes them before every start, and the cookies stay."""
    profile, uid = str(uuid.uuid4()), 20026
    for _ in range(3):
        stack.lease(profile, uid)
        assert stack.probe("navigate", f"{SITE}?login=e2e-browser-h", "20")["ok"] is True
        end(stack)
        assert int(stack.python(SAVED_TABS, profile, user=str(uid))) > 0
    stack.lease(profile, uid)
    opened = [t["url"] for t in json.loads(stack.python(TARGETS)) if t["type"] == "page"]
    front = stack.probe("front", f"{PAGES}/form.html")
    events = stack.current()["lease"]["events"]
    seen = stack.probe("navigate", SITE, "20")
    end(stack)
    assert opened == ["about:blank"], opened
    assert front == {"visibility": "visible", "focus": True}, front
    assert not [e for e in events if e.get("event") == "tab_closed"], events
    assert "Signed in as e2e-browser-h" in seen["text"], seen


TYPE = """
import ctypes, sys
x, t = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXtst.so.6")
x.XOpenDisplay.restype, x.XOpenDisplay.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
x.XStringToKeysym.restype, x.XStringToKeysym.argtypes = ctypes.c_ulong, [ctypes.c_char_p]
x.XKeysymToKeycode.restype = ctypes.c_ubyte
x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x.XKeycodeToKeysym.restype = ctypes.c_ulong
x.XKeycodeToKeysym.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_int]
x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
t.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
t.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 3 + [ctypes.c_ulong]
d = x.XOpenDisplay(b":1")
NAMES = {":": "colon", "/": "slash", "-": "minus", "#": "numbersign"}

def code(name):
    sym = x.XStringToKeysym(name.encode())
    c = x.XKeysymToKeycode(d, sym)
    return c, x.XKeycodeToKeysym(d, c, 0) != sym

def press(c, *mods):
    for m in (*mods, c):
        t.XTestFakeKeyEvent(d, m, 1, 0)
    for m in (c, *reversed(mods)):
        t.XTestFakeKeyEvent(d, m, 0, 0)
    x.XSync(d, 0)

if sys.argv[1] in ("focus", "save"):
    t.XTestFakeMotionEvent(d, 0, 720, 500, 0)
    press(code("l" if sys.argv[1] == "focus" else "s")[0], code("Control_L")[0])
    sys.exit()
def write(text):
    for ch in text:
        c, shifted = code(NAMES.get(ch, ch))
        press(c, *([code("Shift_L")[0]] if shifted else []))

scheme, rest = sys.argv[1].split(":", 1)
write(":" + rest)
press(code("Home")[0])
write(scheme)
press(code("Return")[0])
"""


def type_address(stack: Stack, address: str) -> None:
    """Types the address in the window's address bar, with the X test extension (no xdotool in
    the image): the pointer over the window (no click: the new tab page's tiles are links),
    Ctrl+L, the keys, Return. The scheme is typed last, at the start: typed at the
    end, "chrome" races the address bar's inline completion (chrome.google.com). The focus is
    given a few docker exec round trips to land (no sleep)."""
    stack.execute("browser", "python", "-c", TYPE, "focus", user="1000")
    for _ in range(10):
        window_titles(stack)
    stack.execute("browser", "python", "-c", TYPE, address, user="1000")


# What the person types, the title of the page when it opens, and what shows instead when it is
# blocked (its address, or chrome://policy for its test page, which is off). chrome://policy and
# the new tab page open: the controls.
TYPED = [
    ("chrome://policy", "Policies", None),
    ("chrome://flags", "Experiments", "chrome://flags/"),
    ("chrome://newtab", "New Tab", None),
    ("chrome://policy/test", "Policy Tests", "Policies"),
    ("chrome://settings", "Settings", "chrome://settings/"),
    ("chrome://extensions", "Extensions", "chrome://extensions/"),
    ("chrome://net-internals", "net-internals", "chrome://net-internals/"),
    (
        "chrome://inspect/#devices",
        "Inspect with Chrome Developer Tools",
        "chrome://inspect/#devices",
    ),
    ("about:flags", "Experiments", "chrome://flags/"),
    ("chrome://settings/content", "Settings", "chrome://settings/content"),
]


@pytest.mark.parametrize("purpose", ["signin", "takeover"])
def test_chrome_pages_are_blocked_in_a_persons_session(stack: Stack, purpose: str) -> None:
    """The owner's decision holds for the person's own sessions, which nothing may attach to:
    the address is typed in the window as he would, and the window's title tells what shows."""
    session(stack, purpose, F, UID_F, SITE)
    wait_title(stack, "Example Portal")
    for address, opened, blocked in TYPED:
        type_address(stack, address)
        wait_title(stack, f'"{blocked or opened} - Chromium"')
        if blocked:
            assert f'"{opened} - Chromium"' not in window_titles(stack)
    assert stack.current()["lease"]["purpose"] == purpose
    end(stack)


SAVED = """
import json, os, sys
folder = f"/leases/{sys.argv[1]}/downloads"
print(json.dumps(sorted(os.listdir(folder)) if os.path.isdir(folder) else None))
"""


def test_save_page_as_is_off_in_a_persons_session(stack: Stack) -> None:
    """Ctrl+S in a person's session: with no file dialog (AllowFileSelectionDialogs) Chromium
    turns "Save page as" off, so no dialog opens to write into a folder the person picks, the
    profile among them, and nothing is saved. The control: the next keys reach the window."""
    profile, uid = str(uuid.uuid4()), 20026
    lease = session(stack, "signin", profile, uid, SITE)["lease_id"]
    try:
        wait_title(stack, "Example Portal: signed out")
        stack.execute("browser", "python", "-c", TYPE, "focus", user="1000")
        for _ in range(10):
            window_titles(stack)
        stack.execute("browser", "python", "-c", TYPE, "save", user="1000")
        for _ in range(30):
            assert "Save File" not in window_titles(stack)
        assert json.loads(stack.python(SAVED, lease, user=str(uid))) == []
        type_address(stack, "chrome://policy")
        wait_title(stack, '"Policies - Chromium"')
        assert "Save File" not in window_titles(stack)
    finally:
        if stack.current()["lease"] is not None:
            end(stack)


FLAG_ON = """
import json, sys
path = f"/profiles/{sys.argv[1]}/data/Local State"
state = json.load(open(path))
state.setdefault("browser", {})["enabled_labs_experiments"] = [
    "enable-experimental-web-platform-features"]
json.dump(state, open(path, "w"))
"""

FLAGS_SAVED = """
import json, sys
state = json.load(open(f"/profiles/{sys.argv[1]}/data/Local State"))
print(json.dumps(state.get("browser", {}).get("enabled_labs_experiments", [])))
"""


@pytest.mark.parametrize("purpose", ["signin", "apply_fill"])
def test_a_saved_chrome_flag_does_not_survive_a_restart(stack: Stack, purpose: str) -> None:
    """A flag saved in Local State (chrome://flags before the block, or written by hand) applies
    at every start; Chromium passes this one to its renderers as a switch. prep drops it."""
    sign_in(stack, F, UID_F, "e2e-browser-f")
    end(stack)
    stack.python(FLAG_ON, F, user=str(UID_F))
    assert json.loads(stack.python(FLAGS_SAVED, F, user=str(UID_F))) != []
    if purpose == "signin":
        session(stack, purpose, F, UID_F, SITE)
        wait_title(stack, "signed in as e2e-browser-f")
    else:
        stack.lease(F, UID_F, purpose)
        assert stack.probe("navigate", SITE, "20")["ok"] is True
    pids = [str(pid) for owner, pid, _ in processes(stack) if owner == UID_F]
    argv = stack.python(ARGV_ALL, *pids)
    assert "--type=renderer" in argv
    assert "experimental-web-platform-features" not in argv
    end(stack)
    assert json.loads(stack.python(FLAGS_SAVED, F, user=str(UID_F))) == []


POLICY_SET = '[{"name":"URLBlocklist","value":["example.invalid"],"level":1,"scope":1,"source":0}]'

POLICY_TEST = """
import asyncio, json, sys
from playwright.async_api import async_playwright
from browserd import probe

seen = {}

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{probe.cdp_port()}")
        page = browser.contexts[0].pages[0]
        await page.goto("chrome://policy/test")
        seen["url"], seen["title"] = page.url, await page.title()
        seen["flags"] = []
        try:
            await page.evaluate(
                "p => chrome.send('setLocalTestPolicies', ['b7', p, ''])", sys.argv[1])
            for _ in range(3):
                try:
                    await page.goto("chrome://flags", timeout=20000)
                    seen["flags"].append(await page.title())
                except Exception as exc:
                    seen["flags"].append(str(exc)[:300])
        except Exception as exc:
            seen["flags"].append(type(exc).__name__)

try:
    asyncio.run(main())
except Exception as exc:
    seen["error"] = type(exc).__name__
print(json.dumps(seen))
"""


def test_the_policy_test_page_is_off_in_an_agents_lease(stack: Stack) -> None:
    """chrome://policy/test's "Apply policies" (the message the page sends) replaced the image's
    policy and chrome://flags opened (review of #429). With PolicyTestPageEnabled false the page
    shows chrome://policy, and the same message leaves chrome://flags blocked, or ends the lease
    when Chromium refuses it by stopping."""
    stack.lease(F, UID_F)
    seen = json.loads(stack.python(POLICY_TEST, POLICY_SET))
    lease = stack.current()["lease"]
    if lease is not None:
        end(stack)
    assert seen["title"] == "Policies", seen
    assert seen["url"].rstrip("/") == "chrome://policy", seen
    assert seen["flags"], seen
    assert not [t for t in seen["flags"] if "Experiments" in t], seen
    assert lease is None or all("ERR_BLOCKED_BY_ADMINISTRATOR" in t for t in seen["flags"]), seen


TEST_POLICIES_ON = """
import json, sys
path = f"/profiles/{sys.argv[1]}/data/Local State"
state = json.load(open(path))
state["local_test_policies_for_next_startup"] = sys.argv[2]
json.dump(state, open(path, "w"))
"""

TEST_POLICIES_SAVED = """
import json, sys
state = json.load(open(f"/profiles/{sys.argv[1]}/data/Local State"))
print(json.dumps("local_test_policies_for_next_startup" in state))
"""

NO_BLOCK = '[{"name":"URLBlocklist","value":[],"level":1,"scope":1,"source":0}]'


@pytest.mark.parametrize("purpose", ["signin", "apply_fill"])
def test_a_saved_test_policy_does_not_survive_a_restart(stack: Stack, purpose: str) -> None:
    """chrome://policy/test's "Restart and apply" saves its set in Local State for the next
    start: here one that blocks nothing, which would reopen chrome://flags. prep drops it."""
    sign_in(stack, F, UID_F, "e2e-browser-f")
    end(stack)
    stack.python(TEST_POLICIES_ON, F, NO_BLOCK, user=str(UID_F))
    assert json.loads(stack.python(TEST_POLICIES_SAVED, F, user=str(UID_F))) is True
    # The end in a finally: a failed signin case would leave its lease, and the browser busy (409)
    # for the apply_fill case.
    try:
        if purpose == "signin":
            session(stack, purpose, F, UID_F, SITE)
            wait_title(stack, "signed in as e2e-browser-f")
            type_address(stack, "chrome://flags")
            wait_title(stack, '"chrome://flags/ - Chromium"')
            assert '"Experiments - Chromium"' not in window_titles(stack)
        else:
            stack.lease(F, UID_F, purpose)
            seen = stack.probe("navigate", "chrome://flags", "20")
            assert seen["ok"] is False
            assert "ERR_BLOCKED_BY_ADMINISTRATOR" in seen["detail"]
    finally:
        if stack.current()["lease"] is not None:
            end(stack)
    assert json.loads(stack.python(TEST_POLICIES_SAVED, F, user=str(UID_F))) is False


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


LISTENER = (
    "import socket, time\ns = socket.socket()\ns.bind(('127.0.0.1', 9222))\n"
    "s.listen()\ntime.sleep(600)"
)

# kill(-1) as the uid: every process of that uid but the caller.
KILL_UID = """
import contextlib, os, signal
with contextlib.suppress(ProcessLookupError):
    os.kill(-1, signal.SIGKILL)
"""


@contextlib.contextmanager
def port_listener(stack: Stack, uid: int) -> Iterator[None]:
    """A listener on 9222 under the uid, detached; the lease end reaps it, and the way out kills
    it if a failing test left it running."""
    subprocess.run(
        ["docker", "exec", "-d", "-u", str(uid), stack.container("browser"), "python", "-c",
         LISTENER],
        check=True,
    )  # fmt: skip
    try:
        yield
    finally:
        stack.python(KILL_UID, user=str(uid))


def test_a_port_opened_in_a_persons_session_ends_it(stack: Stack) -> None:
    sign_in(stack, G, UID_G, "e2e-browser-g")
    with port_listener(stack, UID_G):
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
