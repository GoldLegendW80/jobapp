"""The live view (browser B4): the person watches their own browser, and takes the keyboard.

    viewer (noVNC in the page) --wss /vnc/websockify--> Caddy --forward_auth--> api
                                                        Caddy --ws--> browserd :6080 (here)
    browserd --socketpair--> x11vnc -inetd on the display :1, one process per viewer

Nothing listens for VNC in the container: each viewer gets its own x11vnc, started as the
display's user with one end of a socket pair as its stdin and stdout (`-inetd`), so no other
process (a person's Chromium included) can connect to a VNC server, and no VNC password exists to
leak. x11vnc runs with its remote control, its commands and its clipboard off.

Who may open a viewer: only a request that carries `X-Live-Grant`, which Caddy copies from the
api's answer to `GET /api/browser/live/auth` (the person's session, their one use ticket, the
page's origin, and that the person holds the current lease). The grant is signed by the api with
BROWSER_CONTROL_SECRET (`sign_grant`, the api's copy is app/browser/live_router.py), names the
lease and the viewer, lives GRANT_TTL_S and is used once. A grant for another lease than the
current one is refused, and every viewer of a lease is closed within a second of its end.

Input: browserd reads the client's half of the RFB stream (`RfbFilter`) and forwards the keyboard
and the pointer only from the one viewer that took over (`POST /leases/{id}/live/input`, behind
the control secret, called by the api's take over and give back); every other viewer is view
only, whatever its page says. The clipboard, desktop resizing and the xvp power messages are
always dropped; a message the filter does not know closes the viewer (fail closed). An input
forwarded counts as activity on the lease (its idle timer starts again).

The agent's side (`agent_turn`, for B5's tool calls): while the person holds the input the call
waits, until Give back or TAKEOVER_S, then learns that the person used the browser, so the
person's keys and the agent's never mix.

Logs: never a grant, a ticket, a query string, nor a byte of the stream.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import logging
import re
import socket
import struct
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from browserd import config as c
from browserd.leases import LeaseError, Manager

log = logging.getLogger("browserd.live")

PORT = 6080
PATH = "/vnc/websockify"
GRANT_HEADER = "x-live-grant"
GRANT_TTL_S = (
    10  # the api signs a grant this long; one more minute of clock skew at most
)
TAKEOVER_S = (
    900  # the person holds the input this long at most, then the agent has it back
)
PERSON_USED = "The person used the browser"
HEAD_MAX = 8 * 1024
FRAME_MAX = 1024 * 1024  # one websocket frame from a viewer
CUT_MAX = (
    256 * 1024
)  # a clipboard message (always dropped) longer than this closes the viewer
READ_CHUNK = 64 * 1024
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

X11VNC = (
    "x11vnc", "-inetd", "-q", "-nopw", "-norc", "-nocmds", "-noremote", "-nolookup",
    "-display", c.DISPLAY, "-nosel", "-noclipboard", "-nosetclipboard", "-nobell",
    "-noxrecord",
)  # fmt: skip

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
GRANT = re.compile(
    rf"v1\.(?P<lease>{UUID})\.(?P<viewer>{UUID})\.(?P<exp>\d{{1,12}})"
    r"\.(?P<nonce>[0-9a-f]{32})\.(?P<sig>[0-9a-f]{64})"
)


# --- the grant -----------------------------------------------------------------------------------


def _grant_mac(secret: bytes, lease_id: str, viewer: str, exp: int, nonce: str) -> str:
    message = f"jobapp-live-grant|v1|{lease_id}|{viewer}|{exp}|{nonce}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def sign_grant(secret: bytes, lease_id: str, viewer: str, exp: int, nonce: str) -> str:
    """The api's signature, here for the tests; app/browser/live_router.py holds the same."""
    return f"v1.{lease_id}.{viewer}.{exp}.{nonce}.{_grant_mac(secret, lease_id, viewer, exp, nonce)}"


@dataclass(frozen=True)
class Grant:
    lease_id: str
    viewer: str
    exp: int
    nonce: str


def read_grant(secret: bytes, raw: str, now: float) -> Grant | None:
    """The grant if its signature holds and it has not expired, else None. Not yet one use: the
    caller (`Live.admit`) remembers the nonce."""
    found = GRANT.fullmatch(raw or "")
    if not secret or found is None:
        return None
    lease_id, viewer, nonce = found["lease"], found["viewer"], found["nonce"]
    exp = int(found["exp"])
    mac = _grant_mac(secret, lease_id, viewer, exp, nonce)
    if not hmac.compare_digest(mac, found["sig"]):
        return None
    if not now <= exp <= now + GRANT_TTL_S + 60:
        return None
    return Grant(lease_id, viewer, exp, nonce)


# --- the client's half of the RFB stream ---------------------------------------------------------


class Refused(Exception):
    """The viewer sent something the filter does not accept: the viewer is closed."""


# Client to server messages (RFC 6143 and the extensions noVNC 1.4 sends): type -> fixed length,
# or a function of the bytes read so far giving the whole length (None: not enough bytes yet).
PASS, INPUT, DROP = "pass", "input", "drop"


def _set_encodings(
    buf: bytes | bytearray | memoryview, at: int, have: int
) -> int | None:
    return None if have < 4 else 4 + 4 * struct.unpack_from(">H", buf, at + 2)[0]


def _cut_text(buf: bytes | bytearray | memoryview, at: int, have: int) -> int | None:
    if have < 8:
        return None
    length = abs(
        struct.unpack_from(">i", buf, at + 4)[0]
    )  # extended clipboard: negative
    if length > CUT_MAX:
        raise Refused("clipboard too long")
    return 8 + length


def _fence(buf: bytes | bytearray | memoryview, at: int, have: int) -> int | None:
    return None if have < 9 else 9 + buf[at + 8]


def _desktop_size(
    buf: bytes | bytearray | memoryview, at: int, have: int
) -> int | None:
    return None if have < 8 else 8 + 16 * buf[at + 6]


def _qemu(buf: bytes | bytearray | memoryview, at: int, have: int) -> int | None:
    if have < 2:
        return None
    if buf[at + 1] != 0:  # only the extended key event is known
        raise Refused("qemu message")
    return 12


MESSAGES: dict[int, tuple[str, int | Callable[..., int | None]]] = {
    0: (PASS, 20),  # SetPixelFormat
    2: (PASS, _set_encodings),  # SetEncodings
    3: (PASS, 10),  # FramebufferUpdateRequest
    4: (INPUT, 8),  # KeyEvent
    5: (INPUT, 6),  # PointerEvent
    6: (DROP, _cut_text),  # ClientCutText: the clipboard never crosses
    150: (PASS, 10),  # EnableContinuousUpdates
    248: (PASS, _fence),  # ClientFence
    250: (DROP, 4),  # xvp: shutdown, reboot, reset of the machine
    251: (DROP, _desktop_size),  # SetDesktopSize: the agent's screen keeps its size
    255: (INPUT, _qemu),  # QEMU extended key event
}
VERSIONS = {b"RFB 003.003\n": 0, b"RFB 003.007\n": 1, b"RFB 003.008\n": 1}
SECURITY_NONE = 1


class RfbFilter:
    """Reads what a viewer sends to x11vnc and keeps what may pass. Linear in the bytes fed: the
    buffer holds at most one unfinished message."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.stage = "version"  # then "security", "init", "messages"

    def feed(self, data: bytes, allow_input: Callable[[], bool]) -> tuple[bytes, bool]:
        """The bytes to forward, and whether an input message was among them."""
        self.buf += data
        out = bytearray()
        at, inputs = 0, False
        buf = self.buf
        while True:
            have = len(buf) - at
            if self.stage == "version":
                if have < 12:
                    break
                version = bytes(buf[at : at + 12])
                if version not in VERSIONS:
                    raise Refused("protocol version")
                out += version
                at += 12
                self.stage = "security" if VERSIONS[version] else "init"
                continue
            if self.stage == "security":
                if have < 1:
                    break
                if buf[at] != SECURITY_NONE:
                    raise Refused("security type")
                out.append(buf[at])
                at += 1
                self.stage = "init"
                continue
            if self.stage == "init":
                if have < 1:
                    break
                out.append(buf[at])
                at += 1
                self.stage = "messages"
                continue
            if have < 1:
                break
            kind = MESSAGES.get(buf[at])
            if kind is None:
                raise Refused("message type")
            verdict, size = kind
            length = size if isinstance(size, int) else size(buf, at, have)
            if length is None or have < length:
                break
            if verdict == PASS or (verdict == INPUT and allow_input()):
                out += buf[at : at + length]
                inputs = inputs or verdict == INPUT
            at += length
        del buf[:at]
        return bytes(out), inputs


# --- websocket framing (RFC 6455, the server's side) ---------------------------------------------


class Closed(Exception):
    """The viewer closed the websocket, or sent a frame that ends it."""


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """One frame of a viewer: (opcode, payload). Viewers must mask; a frame over FRAME_MAX or a
    text frame ends the connection."""
    head = await reader.readexactly(2)
    opcode, masked, size = head[0] & 0x0F, head[1] & 0x80, head[1] & 0x7F
    if not masked or head[0] & 0x70:
        raise Closed("unmasked or reserved bits")
    if size == 126:
        size = struct.unpack(">H", await reader.readexactly(2))[0]
    elif size == 127:
        size = struct.unpack(">Q", await reader.readexactly(8))[0]
    if size > FRAME_MAX:
        raise Closed("frame too large")
    mask = await reader.readexactly(4)
    payload = await reader.readexactly(size)
    return opcode, unmask(payload, mask)


def unmask(payload: bytes, mask: bytes) -> bytes:
    if not payload:
        return payload
    key = int.from_bytes((mask * (len(payload) // 4 + 1))[: len(payload)], "big")
    return (int.from_bytes(payload, "big") ^ key).to_bytes(len(payload), "big")


def frame(opcode: int, payload: bytes) -> bytes:
    size = len(payload)
    if size < 126:
        head = struct.pack(">BB", 0x80 | opcode, size)
    elif size < 1 << 16:
        head = struct.pack(">BBH", 0x80 | opcode, 126, size)
    else:
        head = struct.pack(">BBQ", 0x80 | opcode, 127, size)
    return head + payload


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode() + WS_GUID).digest()).decode()


# --- x11vnc --------------------------------------------------------------------------------------

Vnc = tuple[asyncio.StreamReader, asyncio.StreamWriter, Callable[[], Awaitable[None]]]


async def spawn_x11vnc() -> Vnc:
    """One x11vnc for one viewer, as the display's user, speaking RFB over a socket pair."""
    ours, theirs = socket.socketpair()
    try:
        proc = await asyncio.create_subprocess_exec(
            *X11VNC,
            stdin=theirs.fileno(), stdout=theirs.fileno(), stderr=asyncio.subprocess.DEVNULL,
            user=c.DISPLAY_UID, group=c.DISPLAY_UID, extra_groups=[], start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "DISPLAY": c.DISPLAY, "HOME": "/tmp"}, cwd="/",
        )  # fmt: skip
    finally:
        theirs.close()
    reader, writer = await asyncio.open_connection(sock=ours)

    async def stop() -> None:
        writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 2)
        if (
            proc.returncode is None
        ):  # browserd has no CAP_KILL: a child of that uid stops it
            from browserd.chromium import as_uid

            await as_uid(
                c.DISPLAY_UID,
                sys.executable,
                "-m",
                "browserd.prep",
                "kill",
                str(proc.pid),
            )

    return reader, writer, stop


# --- the viewers and who holds the input ---------------------------------------------------------


@dataclass(eq=False)
class Viewer:
    lease_id: str
    viewer: str
    writer: asyncio.StreamWriter
    tasks: list["asyncio.Task[Any]"] = field(default_factory=list)
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(Exception):
            self.writer.write(frame(0x8, struct.pack(">H", 1000)))
        for task in self.tasks:
            task.cancel()
        self.writer.close()


class Live:
    """The viewers of the current lease and the one that holds the input. One per browserd."""

    def __init__(
        self,
        manager: Manager,
        secret: bytes,
        *,
        spawn: Callable[[], Awaitable[Vnc]] = spawn_x11vnc,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.manager, self.secret, self.spawn, self.wall = manager, secret, spawn, wall
        self.viewers: set[Viewer] = set()
        self.nonces: dict[str, int] = {}
        self.input_lease: str | None = None
        self.input_viewer: str | None = None
        self.input_until = 0.0
        self.used: set[str] = (
            set()
        )  # leases the person used since the agent's last call
        self.given_back = asyncio.Event()
        self.given_back.set()
        self.watcher: asyncio.Task[None] | None = None

    # The input ------------------------------------------------------------------------------------

    def _lease_id(self) -> str | None:
        lease = self.manager.current()
        return lease.lease_id if lease else None

    def holder(self, lease_id: str) -> str | None:
        """The viewer that holds the input of that lease, or None (the agent has it)."""
        if self.input_lease == lease_id and lease_id == self._lease_id():
            return self.input_viewer
        return None

    def take(self, lease_id: str, viewer: str) -> None:
        if lease_id != self._lease_id():
            raise LeaseError(404, "no_such_lease")
        self.input_lease, self.input_viewer = lease_id, viewer
        self.input_until = self.manager.clock() + TAKEOVER_S
        self.used.add(lease_id)
        self.given_back.clear()

    def give_back(self, lease_id: str | None = None) -> None:
        if lease_id is not None and lease_id != self.input_lease:
            return
        self.input_lease = self.input_viewer = None
        self.given_back.set()

    async def agent_turn(self, lease_id: str) -> str | None:
        """For B5's tool calls, before they act: waits while the person holds the input of that
        lease (Give back, TAKEOVER_S, or the lease's end), then answers PERSON_USED when the
        person took over since the agent's last call, else None."""
        while self.holder(lease_id) is not None:
            await self.given_back.wait()
        if lease_id in self.used:
            self.used.discard(lease_id)
            return PERSON_USED
        return None

    def tick(self) -> None:
        """Every half second: the viewers of an ended lease are closed, and a take over past
        TAKEOVER_S, or of a lease that ended, gives the input back."""
        current = self._lease_id()
        for viewer in list(self.viewers):
            if viewer.lease_id != current:
                viewer.close()
                self.viewers.discard(viewer)
        if self.input_lease is not None and (
            self.input_lease != current or self.manager.clock() >= self.input_until
        ):
            self.give_back()
        self.used &= {current} if current else set()
        now = self.wall()
        self.nonces = {n: exp for n, exp in self.nonces.items() if exp >= now}

    # A viewer -------------------------------------------------------------------------------------

    def admit(self, headers: dict[str, str]) -> Grant | None:
        grant = read_grant(self.secret, headers.get(GRANT_HEADER, ""), self.wall())
        if grant is None or grant.nonce in self.nonces:
            return None
        lease = self.manager.current()
        if lease is None or lease.lease_id != grant.lease_id:
            return None
        self.nonces[grant.nonce] = grant.exp
        return grant

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
                method, target, headers = parse_head(head)
            except (
                ValueError,
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError,
                TimeoutError,
            ):
                writer.write(answer(400))
                return
            if method != "GET" or target.split("?", 1)[0] != PATH:
                writer.write(answer(404))
                return
            key = headers.get("sec-websocket-key", "")
            if (
                headers.get("upgrade", "").lower() != "websocket"
                or "upgrade" not in headers.get("connection", "").lower()
                or headers.get("sec-websocket-version") != "13"
                or not re.fullmatch(r"[A-Za-z0-9+/]{22}==", key)
            ):
                writer.write(answer(400))
                return
            grant = self.admit(headers)
            if grant is None:
                writer.write(answer(403))
                return
            await self.serve_viewer(grant, key, headers, reader, writer)
        finally:
            with contextlib.suppress(Exception):
                await writer.drain()
            writer.close()

    async def serve_viewer(
        self,
        grant: Grant,
        key: str,
        headers: dict[str, str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            vnc_reader, vnc_writer, stop = await self.spawn()
        except OSError as exc:  # x11vnc missing, or the uid change refused
            log.warning("x11vnc did not start: %s", type(exc).__name__)
            writer.write(answer(503))
            return
        offered = [
            p.strip() for p in headers.get("sec-websocket-protocol", "").split(",")
        ]
        protocol = "Sec-WebSocket-Protocol: binary\r\n" if "binary" in offered else ""
        writer.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_key(key)}\r\n{protocol}\r\n"
            ).encode()
        )
        viewer = Viewer(grant.lease_id, grant.viewer, writer)
        self.viewers.add(viewer)
        rfb = RfbFilter()

        def allow() -> bool:
            return self.holder(viewer.lease_id) == viewer.viewer

        async def upstream() -> None:  # the viewer's frames, filtered, to x11vnc
            while True:
                opcode, payload = await read_frame(reader)
                if opcode == 0x8:
                    return
                if opcode == 0x9:
                    writer.write(frame(0xA, payload[:125]))
                    continue
                if opcode == 0xA:
                    continue
                if opcode not in (0x0, 0x2):
                    raise Closed("text frame")
                data, inputs = rfb.feed(payload, allow)
                if inputs:
                    lease = self.manager.current()
                    if lease is not None and lease.lease_id == viewer.lease_id:
                        lease.touch(self.manager.clock())
                if data:
                    vnc_writer.write(data)
                    await vnc_writer.drain()

        async def downstream() -> (
            None
        ):  # x11vnc's bytes to the viewer, as binary frames
            while True:
                data = await vnc_reader.read(READ_CHUNK)
                if not data:
                    return
                writer.write(frame(0x2, data))
                await writer.drain()

        viewer.tasks = [
            asyncio.ensure_future(upstream()),
            asyncio.ensure_future(downstream()),
        ]
        try:
            await asyncio.wait(viewer.tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # A viewer that goes (a reload) keeps the input it took until Give back or the end.
            for task in viewer.tasks:
                task.cancel()
            await asyncio.gather(*viewer.tasks, return_exceptions=True)
            self.viewers.discard(viewer)
            viewer.close()
            await stop()


def parse_head(head: bytes) -> tuple[str, str, dict[str, str]]:
    lines = head.decode("latin-1").split("\r\n")
    method, target, _ = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
    return method, target, headers


def answer(status: int) -> bytes:
    reason = {
        400: "Bad Request",
        403: "Forbidden",
        404: "Not Found",
        503: "Service Unavailable",
    }
    return (
        f"HTTP/1.1 {status} {reason.get(status, 'Error')}\r\nContent-Length: 0\r\n"
        "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
    ).encode()


# --- the control routes and the service ----------------------------------------------------------


def live_of(svc: Any) -> Live:
    """The service's Live, made on first use (browserd/__main__.py makes it at start)."""
    found = getattr(svc, "live", None)
    if found is None:
        found = Live(svc.manager, svc.secret)
        svc.live = found
    return found


async def set_input(svc: Any, req: Any, match: re.Match[str]) -> tuple[int, Any]:
    """POST /leases/{id}/live/input {viewer: <uuid> | null}: that viewer takes the keyboard and
    the mouse, or (null) the agent has them back."""
    body = req.json()
    viewer = body.get("viewer") if isinstance(body, dict) else None
    live = live_of(svc)
    if viewer is None:
        if match["lease_id"] != live._lease_id():
            raise LeaseError(404, "no_such_lease")
        live.give_back(match["lease_id"])
        return 200, {"input": "agent"}
    if not isinstance(viewer, str) or not re.fullmatch(UUID, viewer):
        raise LeaseError(400, "bad_viewer")
    live.take(match["lease_id"], viewer)
    return 200, {"input": "person"}


@dataclass(frozen=True)
class Route:
    """The shape of browserd.app.Route (app.py imports this module, so not that class)."""

    method: str
    pattern: re.Pattern[str]
    handler: Callable[..., Awaitable[tuple[int, Any]]]
    body_max: int = 1024


LEASE_ID = r"(?P<lease_id>[0-9a-f-]{36})"
ROUTES = [Route("POST", re.compile(rf"/leases/{LEASE_ID}/live/input"), set_input)]


async def serve(live: Live, host: str = "0.0.0.0", port: int = PORT) -> asyncio.Server:
    """The viewers' listener on the `browsing` network (Caddy only reaches it), and the watcher
    that closes the viewers of an ended lease within a second."""

    async def watch() -> None:
        while True:
            live.tick()
            await asyncio.sleep(0.5)

    server = await asyncio.start_server(live.handle, host, port, limit=HEAD_MAX)
    live.watcher = asyncio.ensure_future(watch())
    return server
