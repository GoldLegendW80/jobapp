"""browserd's live view (browser/browserd/live.py, browser B4) without a container: the RFB filter,
the grant, who holds the input, and real websockets over loopback against a fake x11vnc.

No test sleeps or reads the wall clock: the grant's clock and the lease's clock are injected, and
every wait is on an event or on bytes. Secrets and ids are made up here.
"""

import asyncio
import base64
import contextlib
import gc
import os
import secrets
import struct
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd import live  # noqa: E402
from browserd.config import Config  # noqa: E402
from browserd.leases import LeaseError, Manager, Request  # noqa: E402

from app.browser import live_router  # noqa: E402

SECRET = b"e2e-live-" + secrets.token_hex(8).encode()
WALL = 1_800_000_000.0
LEASE = str(uuid.uuid4())
VIEWER = str(uuid.uuid4())

HANDSHAKE = b"RFB 003.008\n" + b"\x01" + b"\x01"  # version, security None, ClientInit shared
KEY = struct.pack(">BBxxI", 4, 1, 0x61)  # KeyEvent "a" down
POINTER = struct.pack(">BBHH", 5, 1, 10, 20)
UPDATE = struct.pack(">BBHHHH", 3, 1, 0, 0, 1440, 900)
PIXEL = bytes([0]) + bytes(19)
ENCODINGS = struct.pack(">BxH", 2, 3) + struct.pack(">iii", 0, 7, -223)
CUT = struct.pack(">BxxxI", 6, 5) + b"hello"
CUT_EXTENDED = struct.pack(">Bxxxi", 6, -4) + b"\x00\x00\x00\x01"
FENCE = struct.pack(">BxxxIB", 248, 0, 3) + b"abc"
CONTINUOUS = struct.pack(">BBHHHH", 150, 1, 0, 0, 1440, 900)
XVP = struct.pack(">BxBB", 250, 1, 2)
DESKTOP = struct.pack(">BxHHBx", 251, 800, 600, 1) + bytes(16)
QEMU_KEY = struct.pack(">BBHII", 255, 0, 1, 0x61, 30)


def feed(data: bytes, allow: bool) -> bytes:
    rfb = live.RfbFilter()
    out, _ = rfb.feed(data, lambda: allow)
    return out


# --- the RFB filter ------------------------------------------------------------------------------

PASSING = [UPDATE, PIXEL, ENCODINGS, FENCE, CONTINUOUS]
INPUTS = [KEY, POINTER, QEMU_KEY]
ALWAYS_DROPPED = [CUT, CUT_EXTENDED, XVP, DESKTOP]


@pytest.mark.parametrize("message", PASSING)
@pytest.mark.parametrize("allow", [True, False])
def test_screen_messages_always_pass(message: bytes, allow: bool) -> None:
    assert feed(HANDSHAKE + message, allow) == HANDSHAKE + message


@pytest.mark.parametrize("message", INPUTS)
def test_input_passes_only_for_the_holder(message: bytes) -> None:
    assert feed(HANDSHAKE + message, True) == HANDSHAKE + message
    assert feed(HANDSHAKE + message, False) == HANDSHAKE


@pytest.mark.parametrize("message", ALWAYS_DROPPED)
@pytest.mark.parametrize("allow", [True, False])
def test_clipboard_resize_and_power_never_pass(message: bytes, allow: bool) -> None:
    assert feed(HANDSHAKE + message + UPDATE, allow) == HANDSHAKE + UPDATE


@pytest.mark.parametrize(
    "stream",
    [
        b"RFB 003.889\n",  # a version x11vnc does not speak
        b"RFB 004.000\n",
        b"rfb 003.008\n",
        b"RFB 003.008\n\x02",  # VNC authentication chosen: there is no password to give
        b"RFB 003.008\n\x00",
        b"RFB 003.008\n\x10",
        HANDSHAKE + b"\x01",  # type 1 is the server's, never the client's
        HANDSHAKE + b"\x07",
        HANDSHAKE + b"\xfe",
        HANDSHAKE + b"\xff\x01" + bytes(10),  # a QEMU message other than the key event
        HANDSHAKE + struct.pack(">BxxxI", 6, live.CUT_MAX + 1),
        HANDSHAKE + struct.pack(">Bxxxi", 6, -(live.CUT_MAX + 1)),
    ],
)
def test_anything_unknown_closes_the_viewer(stream: bytes) -> None:
    with pytest.raises(live.Refused):
        live.RfbFilter().feed(stream, lambda: True)


def test_rfb_3_3_has_no_security_byte() -> None:
    stream = b"RFB 003.003\n" + b"\x01" + KEY
    assert feed(stream, False) == b"RFB 003.003\n\x01"
    assert feed(stream, True) == stream


ALL = HANDSHAKE + b"".join(PASSING + INPUTS + ALWAYS_DROPPED + PASSING)


@pytest.mark.parametrize("allow", [True, False])
def test_every_split_position_gives_the_same_stream(allow: bool) -> None:
    whole = feed(ALL, allow)
    for cut in range(len(ALL) + 1):
        rfb = live.RfbFilter()
        first, _ = rfb.feed(ALL[:cut], lambda: allow)
        second, _ = rfb.feed(ALL[cut:], lambda: allow)
        assert first + second == whole, cut
    byte_by_byte = live.RfbFilter()
    assert b"".join(byte_by_byte.feed(bytes([b]), lambda: allow)[0] for b in ALL) == whole


def test_the_input_flag_and_the_buffer_stay_small() -> None:
    rfb = live.RfbFilter()
    _, seen = rfb.feed(HANDSHAKE + UPDATE, lambda: True)
    assert not seen
    _, seen = rfb.feed(KEY + KEY[:3], lambda: True)
    assert seen
    assert len(rfb.buf) == 3
    _, seen = rfb.feed(KEY[3:], lambda: False)
    assert not seen


def test_a_large_stream_is_linear() -> None:
    """Fifty thousand messages in one feed: one pass, the buffer emptied once."""
    stream = HANDSHAKE + (POINTER + UPDATE) * 50_000
    out, seen = live.RfbFilter().feed(stream, lambda: False)
    assert out == HANDSHAKE + UPDATE * 50_000
    assert not seen


# --- the grant -----------------------------------------------------------------------------------


def grant(**overrides: Any) -> str:
    values: dict[str, Any] = {
        "secret": SECRET,
        "lease_id": LEASE,
        "viewer": VIEWER,
        "exp": int(WALL) + 10,
        "nonce": secrets.token_hex(16),
    }
    values.update(overrides)
    return live.sign_grant(**values)


def test_the_apis_grant_is_the_one_browserd_reads() -> None:
    made = live_router.sign_grant(SECRET, LEASE, VIEWER, int(WALL) + 10)
    found = live.read_grant(SECRET, made, WALL)
    assert found is not None
    assert (found.lease_id, found.viewer, found.exp) == (LEASE, VIEWER, int(WALL) + 10)
    assert live.GRANT_TTL_S == live_router.GRANT_TTL_S


@pytest.mark.parametrize(
    ("raw", "now"),
    [
        (grant(), WALL + 11),  # expired
        (grant(exp=int(WALL) + 10 + 61), WALL),  # too far ahead
        (grant(secret=b"another secret"), WALL),
        (grant()[:-1] + "0", WALL),  # signature changed
        (grant().replace(LEASE, str(uuid.uuid4())), WALL),  # another lease, same signature
        (grant().replace(VIEWER, str(uuid.uuid4())), WALL),
        (grant().upper(), WALL),
        (grant() + " ", WALL),
        ("v2" + grant()[2:], WALL),
        ("", WALL),
        ("v1." * 40, WALL),
    ],
    ids=[  # the values are random: xdist's workers must collect the same names
        "expired",
        "too_far_ahead",
        "other_secret",
        "signature_changed",
        "other_lease",
        "other_viewer",
        "upper_case",
        "trailing_space",
        "other_version",
        "empty",
        "garbage",
    ],
)
def test_a_grant_that_does_not_hold_is_refused(raw: str, now: float) -> None:
    assert live.read_grant(SECRET, raw, now) is None


def test_no_secret_admits_nobody() -> None:
    assert (
        live.read_grant(b"", live.sign_grant(b"", LEASE, VIEWER, int(WALL), "0" * 32), WALL) is None
    )


# --- a browserd with a lease ---------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeChromium:
    async def stop(self) -> None:
        pass

    async def kill(self) -> None:
        pass


def lease_request() -> Request:
    return Request.parse(
        {"user_id": "a", "profile_id": str(uuid.uuid4()), "uid": 20000, "purpose": "apply_fill",
         "automation": True}
    )  # fmt: skip


class FakeVnc:
    """Stands for x11vnc: sends a greeting, records every byte it is sent."""

    def __init__(self) -> None:
        self.received = bytearray()
        self.got = asyncio.Event()
        self.stopped = 0
        self.stop_seen = asyncio.Event()

    async def spawn(self) -> live.Vnc:
        ours, theirs = await socket_pair()
        reader, writer = theirs
        writer.write(b"RFB 003.008\n")

        async def record() -> None:
            while data := await reader.read(65536):
                self.received += data
                self.got.set()

        task = asyncio.ensure_future(record())

        async def stop() -> None:
            self.stopped += 1
            self.stop_seen.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for end in (writer, ours[1]):
                end.close()
                with contextlib.suppress(OSError):
                    await end.wait_closed()

        return ours[0], ours[1], stop


async def socket_pair() -> tuple[
    tuple[asyncio.StreamReader, asyncio.StreamWriter],
    tuple[asyncio.StreamReader, asyncio.StreamWriter],
]:
    import socket

    a, b = socket.socketpair()
    return await asyncio.open_connection(sock=a), await asyncio.open_connection(sock=b)


class World:
    def __init__(self) -> None:
        self.clock = Clock()
        self.wall = WALL

        async def start(_: Request, __: str) -> FakeChromium:
            return FakeChromium()

        self.manager = Manager(Config(secret=SECRET), start, self.clock)
        self.vnc = FakeVnc()
        self.live = live.Live(self.manager, SECRET, spawn=self.vnc.spawn, wall=lambda: self.wall)

    async def lease(self) -> str:
        made, _ = await self.manager.acquire(lease_request())
        return made.lease_id

    def grant(self, lease_id: str, viewer: str = VIEWER) -> str:
        return live_router.sign_grant(SECRET, lease_id, viewer, int(self.wall) + 10)


OPENED: list[asyncio.StreamWriter] = []
SERVERS: list[asyncio.Server] = []


def run(test: Callable[[World], Awaitable[None]]) -> None:
    """Runs the test, then closes every connection and server it opened, and waits for the
    viewers browserd still serves to end."""

    async def go() -> None:
        world = World()
        try:
            await test(world)
        finally:
            for writer in OPENED:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
            for server in SERVERS:
                server.close()
                await asyncio.wait_for(server.wait_closed(), 5)
            OPENED.clear()
            SERVERS.clear()
            # A viewer whose client left may still be stopping its x11vnc: let it finish.
            rest = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            await asyncio.wait_for(asyncio.gather(*rest, return_exceptions=True), 5)

    asyncio.run(go())
    gc.collect()  # a leaked socket fails this test, not the next one


def test_take_and_give_back() -> None:
    async def go(w: World) -> None:
        lease_id = await w.lease()
        assert w.live.holder(lease_id) is None
        w.live.take(lease_id, VIEWER)
        assert w.live.holder(lease_id) == VIEWER
        other = str(uuid.uuid4())
        w.live.take(lease_id, other)  # a second tab takes it: only one holds the input
        assert w.live.holder(lease_id) == other
        w.live.give_back(lease_id)
        assert w.live.holder(lease_id) is None
        with pytest.raises(LeaseError) as refused:
            w.live.take(str(uuid.uuid4()), VIEWER)
        assert refused.value.status == 404

    run(go)


def test_the_agent_waits_while_the_person_holds_the_input() -> None:
    async def go(w: World) -> None:
        lease_id = await w.lease()
        assert await w.live.agent_turn(lease_id) is None
        w.live.take(lease_id, VIEWER)
        turn = asyncio.ensure_future(w.live.agent_turn(lease_id))
        await asyncio.sleep(0)
        assert not turn.done()
        w.live.give_back(lease_id)
        assert await turn == live.PERSON_USED
        assert await w.live.agent_turn(lease_id) is None  # told once

    run(go)


def test_the_take_over_ends_after_fifteen_minutes() -> None:
    async def go(w: World) -> None:
        lease_id = await w.lease()
        w.live.take(lease_id, VIEWER)
        turn = asyncio.ensure_future(w.live.agent_turn(lease_id))
        w.clock.now += live.TAKEOVER_S - 1
        w.live.tick()
        await asyncio.sleep(0)
        assert not turn.done()
        assert w.live.holder(lease_id) == VIEWER
        w.clock.now += 1
        w.live.tick()
        assert await turn == live.PERSON_USED
        assert w.live.holder(lease_id) is None

    run(go)


def test_the_lease_end_gives_the_input_back_and_wakes_the_agent() -> None:
    async def go(w: World) -> None:
        lease_id = await w.lease()
        w.live.take(lease_id, VIEWER)
        turn = asyncio.ensure_future(w.live.agent_turn(lease_id))
        await w.manager.release(lease_id, "released")
        w.live.tick()
        await turn  # woken: the call itself then meets the end of its lease (LeaseEnded)
        assert w.live.input_lease is None
        assert not w.live.used

    run(go)


# --- websockets over loopback --------------------------------------------------------------------


class WsClient:
    """A minimal websocket client, masked as a browser's frames are."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer

    def send(self, payload: bytes, opcode: int = 0x2) -> None:
        mask = os.urandom(4)
        size = len(payload)
        head = bytes([0x80 | opcode])
        if size < 126:
            head += bytes([0x80 | size])
        else:
            head += bytes([0x80 | 126]) + struct.pack(">H", size)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.writer.write(head + mask + masked)

    async def recv(self) -> tuple[int, bytes]:
        head = await self.reader.readexactly(2)
        size = head[1] & 0x7F
        if size == 126:
            size = struct.unpack(">H", await self.reader.readexactly(2))[0]
        elif size == 127:
            size = struct.unpack(">Q", await self.reader.readexactly(8))[0]
        return head[0] & 0x0F, await self.reader.readexactly(size)


async def open_ws(
    port: int, headers: dict[str, str], path: str = live.PATH + "?ticket=x"
) -> tuple[str, WsClient]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    OPENED.append(writer)
    key = base64.b64encode(os.urandom(16)).decode()
    sent = {
        "Host": "app.example.org",
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": key,
        "Sec-WebSocket-Protocol": "binary",
        **headers,
    }
    head = f"GET {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in sent.items() if v)
    writer.write((head + "\r\n").encode())
    answer = (await reader.readuntil(b"\r\n\r\n")).decode()
    if answer.startswith("HTTP/1.1 101"):
        assert f"Sec-WebSocket-Accept: {live.accept_key(key)}" in answer
        assert "Sec-WebSocket-Protocol: binary" in answer
    return answer.split("\r\n", 1)[0], WsClient(reader, writer)


async def served(w: World) -> tuple[asyncio.Server, int]:
    server = await asyncio.start_server(w.live.handle, "127.0.0.1", 0, limit=live.HEAD_MAX)
    SERVERS.append(server)
    return server, server.sockets[0].getsockname()[1]


async def until(event: asyncio.Event, check: Callable[[], bool]) -> None:
    while not check():
        event.clear()
        await asyncio.wait_for(event.wait(), 5)


def test_a_viewer_sees_the_screen_and_types_only_after_taking_over() -> None:
    async def go(w: World) -> None:
        lease_id = await w.lease()
        _server, port = await served(w)
        status, ws = await open_ws(port, {"X-Live-Grant": w.grant(lease_id)})
        assert status == "HTTP/1.1 101 Switching Protocols"
        opcode, greeting = await ws.recv()
        assert (opcode, greeting) == (0x2, b"RFB 003.008\n")

        # Split across frames as a browser may: the filter sees one stream.
        ws.send(HANDSHAKE[:5])
        ws.send(HANDSHAKE[5:] + KEY + UPDATE)
        await until(w.vnc.got, lambda: w.vnc.received.endswith(UPDATE))
        assert bytes(w.vnc.received) == HANDSHAKE + UPDATE  # view only: the key never left

        idle_from = w.manager.current().last_input  # type: ignore[union-attr]
        w.clock.now += 30
        w.live.take(lease_id, VIEWER)
        ws.send(KEY + UPDATE)
        await until(w.vnc.got, lambda: bytes(w.vnc.received).endswith(KEY + UPDATE))
        assert w.manager.current().last_input == idle_from + 30  # type: ignore[union-attr]

        w.live.give_back(lease_id)
        ws.send(POINTER + UPDATE)
        await until(w.vnc.got, lambda: w.vnc.received.count(UPDATE) == 3)
        assert POINTER not in w.vnc.received

    run(go)


def test_two_tabs_see_it_and_only_the_holder_types() -> None:
    async def go(w: World) -> None:
        lease_id = await w.lease()
        _server, port = await served(w)
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        _, ws1 = await open_ws(port, {"X-Live-Grant": w.grant(lease_id, first)})
        vnc1 = w.vnc
        w.vnc = FakeVnc()
        w.live.spawn = w.vnc.spawn
        _, ws2 = await open_ws(port, {"X-Live-Grant": w.grant(lease_id, second)})
        vnc2 = w.vnc
        for ws in (ws1, ws2):
            assert await ws.recv() == (0x2, b"RFB 003.008\n")
        w.live.take(lease_id, second)
        ws1.send(HANDSHAKE + KEY + UPDATE)
        ws2.send(HANDSHAKE + KEY + UPDATE)
        await until(vnc1.got, lambda: vnc1.received.endswith(UPDATE))
        await until(vnc2.got, lambda: vnc2.received.endswith(UPDATE))
        assert bytes(vnc1.received) == HANDSHAKE + UPDATE
        assert bytes(vnc2.received) == HANDSHAKE + KEY + UPDATE

    run(go)


def test_the_viewer_is_closed_when_the_lease_ends() -> None:
    async def go(w: World) -> None:
        lease_id = await w.lease()
        _server, port = await served(w)
        _, ws = await open_ws(port, {"X-Live-Grant": w.grant(lease_id)})
        await ws.recv()
        await w.manager.release(lease_id, "lease_max")
        w.live.tick()
        assert await asyncio.wait_for(ws.recv(), 5) == (0x8, struct.pack(">H", 1000))
        assert await asyncio.wait_for(ws.reader.read(), 5) == b""
        assert not w.live.viewers
        await asyncio.wait_for(w.vnc.stop_seen.wait(), 5)  # the viewer's x11vnc is stopped

    run(go)


def test_refusals() -> None:
    async def go(w: World) -> None:
        lease_id = await w.lease()
        _server, port = await served(w)
        used = w.grant(lease_id)
        status, ws = await open_ws(port, {"X-Live-Grant": used})
        assert status.startswith("HTTP/1.1 101")
        ws.writer.close()
        cases = [
            ({"X-Live-Grant": used}, live.PATH, "403"),  # one use
            ({}, live.PATH, "403"),  # no grant
            ({"X-Live-Grant": w.grant(str(uuid.uuid4()))}, live.PATH, "403"),  # not the lease
            ({"X-Live-Grant": w.grant(lease_id)}, "/vnc/other", "404"),
            ({"X-Live-Grant": w.grant(lease_id)}, "/leases/current", "404"),
            ({"X-Live-Grant": w.grant(lease_id), "Upgrade": ""}, live.PATH, "400"),
            ({"X-Live-Grant": w.grant(lease_id), "Sec-WebSocket-Version": "8"}, live.PATH, "400"),
        ]
        for headers, path, expected in cases:
            status, _ = await open_ws(port, headers, path)
            assert status.split(" ")[1] == expected, (headers, path)
        w.wall += 11  # the grant of the case below expired on its way
        status, _ = await open_ws(
            port,
            {
                "X-Live-Grant": w.grant(lease_id).replace(
                    str(int(w.wall) + 10), str(int(w.wall) - 1)
                )
            },
        )
        assert status.split(" ")[1] == "403"
        assert w.vnc.stopped == 1  # only the admitted viewer started an x11vnc

    run(go)


def test_a_text_frame_or_an_unknown_message_closes_the_viewer() -> None:
    async def go(w: World) -> None:
        lease_id = await w.lease()
        _server, port = await served(w)
        for bad in [(b"hello", 0x1), (HANDSHAKE + b"\x07", 0x2)]:
            _, ws = await open_ws(port, {"X-Live-Grant": w.grant(lease_id)})
            await ws.recv()
            ws.send(*bad)
            rest = await asyncio.wait_for(ws.reader.read(), 5)
            assert rest.endswith(b"")
            assert not w.live.viewers

    run(go)


def test_the_input_route_of_the_control_api() -> None:
    async def go(w: World) -> None:
        from browserd import app as browserd_app

        lease_id = await w.lease()
        service = browserd_app.Service(w.manager, SECRET)
        service.live = w.live  # type: ignore[attr-defined]
        route = next(r for r in browserd_app.ROUTES if "live/input" in r.pattern.pattern)
        match = route.pattern.fullmatch(f"/leases/{lease_id}/live/input")
        assert match is not None

        def req(body: bytes) -> Any:
            return browserd_app.HttpRequest("POST", "/", {}, body)

        assert await route.handler(service, req(f'{{"viewer": "{VIEWER}"}}'.encode()), match) == (
            200,
            {"input": "person"},
        )
        assert w.live.holder(lease_id) == VIEWER
        assert await route.handler(service, req(b'{"viewer": null}'), match) == (
            200,
            {"input": "agent"},
        )
        for body in (b'{"viewer": "../x"}', b'{"viewer": 3}'):
            with pytest.raises(LeaseError):
                await route.handler(service, req(body), match)
        other = route.pattern.fullmatch(f"/leases/{uuid.uuid4()}/live/input")
        assert other is not None
        with pytest.raises(LeaseError) as gone:
            await route.handler(service, req(b'{"viewer": null}'), other)
        assert gone.value.status == 404

    run(go)
