"""Browser B12a in the real container (the stack of test_browser_container.py, its own project):
the sign in hand off of an agent's lease, driven through browserd's control and tool API from
where the api and the worker stand.

What runs: the tool on an apply lease that nobody watches, and on a `sweep` lease that a viewer
watches, answers `nobody_watching` and Chromium is not restarted; with a live view open, the tool
answers `waiting_person`, Chromium runs again with no listening port and no debugging switch at
the tab's address, its query string kept; the person signs in with the live view's keyboard
(the RFB key events of a real viewer, as noVNC sends them); "done" brings Chromium back with CDP
at the same address and the agent's snapshot reads the cookie the person's sign in set; "done"
twice restarts once; in the person's phase chrome://flags is blocked and a port the uid opens
ends the lease `debugging_port`; after "done" the allow list is back (the window the person
opened off the list is never listed nor read, and a link off it clicked by the agent answers
`host_not_allowed`); Cancel releases the lease `signin_cancelled` with no Chromium left; the
typed name is in no log of browserd.

The deadline on an injected clock, the Redis restart and the api's routes run without Docker in
test_b12a_unit.py and test_b12a_api.py. The fixture site is tests/browser_pages/b12/login.html.
Marked `docker`, like the module it borrows the stack from.
"""

import json
import secrets
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.browser.test_b5_container import ref
from tests.browser.test_b7_container import (
    browser_argv,
    listening,
    port_listener,
    type_address,
    wait_title,
    window_titles,
)
from tests.browser.test_browser_container import PAGES, Stack, no_lease_left, processes, stack

__all__ = ["no_lease_left", "stack"]

pytestmark = [pytest.mark.docker]

LOGIN = f"{PAGES}/b12/login.html?job=42&step=apply"
WHERE = "job 42, step apply"
TOOL = "browser_request_signin"
TOOL_CALL = """
import http.client, json, sys
path, secret, token, body = sys.argv[1:5]
conn = http.client.HTTPConnection("browser", 9300, timeout=120)
conn.request("POST", path, body=body.encode(), headers={"Authorization": "Bearer " + secret,
    "X-Lease-Token": token, "Content-Type": "application/json"})
answer = conn.getresponse()
out = answer.read()
print(json.dumps({"status": answer.status, "body": json.loads(out) if out else None}))
"""
# A live view as Caddy hands it to browserd: a websocket with the api's grant, then RFB 3.8.
# `call` keeps the view open while it calls the tool; `type` sends key events, then asks for a
# full screen update, whose answer comes after x11vnc has handled the keys.
VIEWER = """
import base64, hashlib, hmac, http.client, os, socket, struct, sys, time, uuid
secret, lease_id, token, mode, payload = sys.argv[1:6]
viewer, nonce, exp = str(uuid.uuid4()), os.urandom(16).hex(), int(time.time()) + 5
message = f"jobapp-live-grant|v1|{lease_id}|{viewer}|{exp}|{nonce}".encode()
mac = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
sock = socket.create_connection(("browser", 6080), timeout=60)
key = base64.b64encode(os.urandom(16)).decode()
sock.sendall((
    "GET /vnc/websockify HTTP/1.1\\r\\nHost: browser\\r\\nUpgrade: websocket\\r\\n"
    "Connection: Upgrade\\r\\nSec-WebSocket-Version: 13\\r\\nSec-WebSocket-Protocol: binary\\r\\n"
    f"Sec-WebSocket-Key: {key}\\r\\n"
    f"X-Live-Grant: v1.{lease_id}.{viewer}.{exp}.{nonce}.{mac}\\r\\n\\r\\n"
).encode())
head = b""
while not head.endswith(b"\\r\\n\\r\\n"):
    one = sock.recv(1)
    if not one:
        break
    head += one
if not head.startswith(b"HTTP/1.1 101"):
    raise SystemExit("viewer refused: " + head[:40].decode("latin-1"))

def exact(n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise SystemExit("the view was closed")
        data += chunk
    return data

stream = bytearray()

def rfb(n):
    while len(stream) < n:
        first, second = exact(2)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack(">H", exact(2))[0]
        elif size == 127:
            size = struct.unpack(">Q", exact(8))[0]
        data = exact(size)
        if first & 0x0F == 8:
            raise SystemExit("the view was closed")
        stream.extend(data)
    out = bytes(stream[:n])
    del stream[:n]
    return out

def send(data):
    mask = os.urandom(4)
    size = len(data)
    if size < 126:
        head = struct.pack(">BB", 0x82, 0x80 | size)
    else:
        head = struct.pack(">BBH", 0x82, 0xFE, size)
    sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

if mode == "call":
    conn = http.client.HTTPConnection("browser", 9300, timeout=120)
    conn.request("POST", f"/leases/{lease_id}/tools/browser_request_signin", body=payload.encode(),
        headers={"Authorization": "Bearer " + secret, "X-Lease-Token": token,
                 "Content-Type": "application/json"})
    answer = conn.getresponse()
    print(answer.status, answer.read().decode())
else:
    send(rfb(12))
    types = rfb(rfb(1)[0])
    if 1 not in types:
        raise SystemExit("no security type None")
    send(b"\\x01")
    if rfb(4) != b"\\x00\\x00\\x00\\x00":
        raise SystemExit("security refused")
    send(b"\\x01")
    init = rfb(24)
    rfb(struct.unpack(">I", init[20:24])[0])
    for keysym in [int(k, 16) for k in payload.split(",")]:
        send(struct.pack(">BBHI", 4, 1, 0, keysym) + struct.pack(">BBHI", 4, 0, 0, keysym))
    send(struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1))
    rfb(1)
    print("typed")
sock.close()
"""
TAB, ENTER = 0xFF09, 0xFF0D


class Agent:
    """An apply lease of its own profile, uid and token, on the fixture site's host."""

    def __init__(self, stack: Stack, uid: int, purpose: str = "apply_fill") -> None:
        self.stack, self.uid = stack, uid
        body = {"user_id": f"u{uid}", "profile_id": str(uuid.uuid4()), "uid": uid,
                "purpose": purpose, "hosts": ["pages"]}  # fmt: skip
        answer = stack.call("POST", "/leases", body)
        assert answer["status"] == 201, answer
        self.id, self.token = answer["body"]["lease_id"], answer["body"]["lease_token"]

    def call(self, name: str, **args: Any) -> dict[str, Any]:
        out = self.stack.execute(
            "client", "python", "-I", "-c", TOOL_CALL, f"/leases/{self.id}/tools/{name}",
            self.stack.secret, self.token, json.dumps(args),
        )  # fmt: skip
        return json.loads(out)

    def tool(self, name: str, **args: Any) -> dict[str, Any]:
        answer = self.call(name, **args)
        assert answer["status"] == 200, answer
        return answer["body"]

    def viewer(self, mode: str, payload: str) -> str:
        return self.stack.execute(
            "client", "python", "-I", "-c", VIEWER, self.stack.secret, self.id, self.token, mode,
            payload,
        ).strip()  # fmt: skip

    def watched_call(self, **args: Any) -> dict[str, Any]:
        status, _, body = self.viewer("call", json.dumps(args)).partition(" ")
        assert status == "200", body
        return json.loads(body)

    def type(self, text: str) -> None:
        keys = [TAB, *(ord(ch) for ch in text), ENTER]
        assert self.viewer("type", ",".join(f"{k:x}" for k in keys)) == "typed"

    def handoff(self, action: str) -> dict[str, Any]:
        return self.stack.call("POST", f"/leases/{self.id}/handoff/{action}")

    def chromium(self) -> list[int]:
        return sorted(pid for owner, pid, _ in processes(self.stack) if owner == self.uid)


@pytest.fixture
def agent(stack: Stack) -> Iterator[Agent]:
    made = Agent(stack, 20000 + secrets.randbelow(30000))
    yield made
    stack.end()


def test_nobody_watching_answers_at_once_and_restarts_nothing(stack: Stack, agent: Agent) -> None:
    assert agent.tool("browser_navigate", url=LOGIN)["ok"]
    before = agent.chromium()
    answer = agent.tool(TOOL, site="Example Jobs", reason="The form needs an account.")
    assert answer["error"] == "nobody_watching", answer
    assert agent.chromium() == before
    assert stack.current()["lease"]["state"] == "active"


def test_a_sweep_lease_with_a_viewer_answers_nobody_watching(stack: Stack) -> None:
    sweep = Agent(stack, 20000 + secrets.randbelow(30000), purpose="sweep")
    try:
        assert sweep.tool("browser_navigate", url=LOGIN)["ok"]
        before = sweep.chromium()
        answer = sweep.watched_call(site="Example Jobs", reason="A login wall.")
        assert answer["error"] == "nobody_watching", answer
        assert sweep.chromium() == before
    finally:
        stack.end()


def test_the_person_signs_in_without_cdp_and_the_agent_reads_the_cookie(
    stack: Stack, agent: Agent
) -> None:
    name = f"e2e-{secrets.token_hex(5)}"
    assert agent.tool("browser_navigate", url=LOGIN)["ok"]
    assert listening(stack, agent.uid)  # the control: the agent's Chromium listens

    answer = agent.watched_call(site="Example Jobs", reason="The form needs an account.")
    assert answer["error"] == "waiting_person", answer
    assert 0 < answer["left_s"] <= 1800
    lease = stack.current()["lease"]
    assert (lease["state"], lease["wait_reason"], lease["wait_site"]) == (
        "waiting_person", "signin", "Example Jobs",
    )  # fmt: skip

    # The person's Chromium: the noted address with its query, and nothing to attach to.
    wait_title(stack, f"Example Jobs: signed out, {WHERE}")
    assert listening(stack, agent.uid) == []
    argv = browser_argv(stack, agent.uid)
    assert not [a for a in argv if "remote-debugging" in a], argv
    refused = agent.call("browser_snapshot")
    assert refused["status"] == 409, refused

    agent.type(name)
    wait_title(stack, f"Example Jobs: signed in as {name}, {WHERE}")

    first = agent.handoff("done")
    assert first["status"] == 200, first
    assert first["body"]["restarted"] is True
    again = agent.handoff("done")
    assert again["status"] == 200, again
    assert again["body"]["restarted"] is False
    assert stack.current()["lease"]["state"] == "active"
    assert listening(stack, agent.uid)

    seen = agent.tool("browser_snapshot")
    assert seen["ok"], seen
    assert f"Signed in as {name}" in seen["snapshot"]
    assert f"Opened at {WHERE}" in seen["snapshot"]
    assert name not in stack.run("logs", "browser")


def test_after_done_the_allow_list_is_back_with_cdp(stack: Stack, agent: Agent) -> None:
    name = f"e2e-{secrets.token_hex(5)}"
    assert agent.tool("browser_navigate", url=LOGIN)["ok"]
    answer = agent.watched_call(site="Example Jobs", reason="The form needs an account.")
    assert answer["error"] == "waiting_person", answer
    wait_title(stack, f"Example Jobs: signed out, {WHERE}")
    # The person's own navigation is free: signing in opens a window on client:8080, off the
    # agent's list (the proxy refuses that address, so the window stays off the list).
    agent.type(name)
    wait_title(stack, f"Example Jobs: signed in as {name}, {WHERE}")
    assert agent.handoff("done")["status"] == 200

    listed = agent.tool("browser_tabs", action="list")
    assert listed["ok"], listed
    # The tab the noted address opened in, the only one (#488): pages only, never client:8080.
    assert {t["url"] for t in listed["tabs"]} == {LOGIN}, listed
    seen = agent.tool("browser_snapshot")
    assert seen["ok"], seen
    assert f"Signed in as {name}" in seen["snapshot"]

    # A link off the list, clicked by the agent: refused, the tab taken back to the list.
    away = agent.tool("browser_click", ref=ref(seen["snapshot"], "link", "Elsewhere"))
    assert away["error"] == "host_not_allowed", away
    assert away["url"] == LOGIN, away
    tabs = agent.tool("browser_tabs", action="list")["tabs"]
    assert {t["url"] for t in tabs} == {LOGIN}, tabs


def test_cancel_releases_the_lease_and_leaves_no_chromium(stack: Stack) -> None:
    agent = Agent(stack, 20000 + secrets.randbelow(30000))
    assert agent.tool("browser_navigate", url=LOGIN)["ok"]
    answer = agent.watched_call(site="Example Jobs", reason="A login wall.")
    assert answer["error"] == "waiting_person", answer
    wait_title(stack, f"Example Jobs: signed out, {WHERE}")

    ended = agent.handoff("cancel")
    assert ended["status"] == 200, ended
    assert ended["body"]["reason"] == "signin_cancelled"
    assert stack.current()["lease"] is None
    assert agent.chromium() == []
    assert agent.handoff("done")["status"] == 404


def test_the_persons_phase_blocks_chrome_pages_and_ends_on_a_port(
    stack: Stack, agent: Agent
) -> None:
    """The person's Chromium of a hand off is a person's session like any other (B7, owner's
    decision of 2026-10-20): every chrome:// page but the image's few is blocked, and a port its
    uid opens ends the lease `debugging_port`."""
    assert agent.tool("browser_navigate", url=LOGIN)["ok"]
    answer = agent.watched_call(site="Example Jobs", reason="The form needs an account.")
    assert answer["error"] == "waiting_person", answer
    wait_title(stack, f"Example Jobs: signed out, {WHERE}")
    type_address(stack, "chrome://flags")
    wait_title(stack, '"chrome://flags/ - Chromium"')
    assert '"Experiments - Chromium"' not in window_titles(stack)
    type_address(stack, "chrome://policy")
    wait_title(stack, '"Policies - Chromium"')
    with port_listener(stack, agent.uid):
        ended = stack.wait_ended()
        assert ended["last_end"]["reason"] == "debugging_port"
        assert agent.chromium() == []
