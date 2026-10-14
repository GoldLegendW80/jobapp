"""The egress proxy of Chromium, on 127.0.0.1:3128 inside the container (SPEC-browser, "Isolation
and network": a hostile page reaches the internet and nothing else).

Chromium runs with --proxy-server pointing here, --proxy-bypass-list=<-loopback> (loopback goes
through the proxy too) and no UDP outside the proxy (WebRTC), so every connection a page opens
comes here. The proxy resolves the name itself, refuses unless every address is global unicast
(no loopback, private, link local and so cloud metadata 169.254.169.254, carrier grade NAT and so
the tailnet's 100.64.0.0/10, unique local IPv6, multicast or reserved), and connects to the
address it checked, so a DNS answer that changes in between cannot rebind it. That also refuses
the app's own services on the `browsing` network (api, worker, caddy), which the host firewall
rule of B6 (`DOCKER-USER` on the `egress` subnet) does not see. Both layers stay: B6's rule
holds for whatever in the container does not use the proxy.

Before anything is resolved the request head must be plain HTTP/1.x (parse_head): a CONNECT
`host:port` or an absolute `http://` target whose host is a DNS name or an IPv6 literal, a port
in PORTS, header lines with a token name, a body framed one way. Anything else answers 400. The
upstream receives one request with the Host the proxy checked and `Connection: close`.

The test stack names its pages server in BROWSER_PROXY_ALLOW_PRIVATE (a private Docker address,
still on a port of PORTS); compose.yml never sets it.

Declared open point: what is checked is the address, not the site. A public server that also
answers for a private virtual host is reached as the Host the proxy writes, the target's name.
"""

import asyncio
import contextlib
import ipaddress
import logging
import socket
from typing import NamedTuple

log = logging.getLogger("browserd.proxy")

PORTS = frozenset({80, 443, 8080, 8443})
HEAD_MAX = 64 * 1024
# Chromium opens at most 32 sockets to one proxy for ordinary requests and up to 255 WebSockets
# (tunnels here), so 512 open connections never constrain a page and bound what one can hold.
CONNECTIONS_MAX = 512
# A connection with no byte in either direction for this long is closed: a page cannot keep idle
# tunnels open for ever. Five minutes, the lease's own idle timeout (BROWSER_IDLE_S).
IDLE_S = 300.0


def _networks(*cidrs: str) -> tuple:
    return tuple(ipaddress.ip_network(n) for n in cidrs)


# The address rule names what it accepts, so anything it does not know is refused. It lists the
# special purpose blocks itself (RFC 6890 and its updates) on top of `is_global`, whose tables
# changed between Python point releases (CVE-2024-4032; the image runs Debian's 3.11).
# An IPv4 is accepted when it is in none of these blocks and Python calls it global.
REFUSED_V4 = _networks(
    "0.0.0.0/8",  # this network
    "10.0.0.0/8",
    "100.64.0.0/10",  # carrier grade NAT, and so the tailnet
    "127.0.0.0/8",
    "169.254.0.0/16",  # link local, and so cloud metadata
    "172.16.0.0/12",
    "192.0.0.0/24",  # IETF protocol assignments
    "192.0.2.0/24",
    "192.88.99.0/24",  # the 6to4 relay
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/3",  # multicast, the reserved 240/4 and the broadcast address
)
# An IPv6 is accepted only inside the global unicast block 2000::/3, outside these blocks, and
# global for Python. Everything outside 2000::/3 is refused: loopback, unspecified, IPv4
# compatible (::/96), NAT64 (64:ff9b::/96, 64:ff9b:1::/48), IPv4 translated (::ffff:0:0:0/96),
# unique local, link local, multicast. Inside it, 6to4 and Teredo carry an IPv4 that a gateway
# on the way may turn into a private one. IPv4 mapped addresses (::ffff:0:0/96) are checked as
# the IPv4 they map.
GLOBAL_V6 = ipaddress.ip_network("2000::/3")
REFUSED_V6 = _networks(
    "2001::/23",  # IETF protocol assignments, Teredo (2001::/32) among them
    "2001:db8::/32",  # documentation
    "2002::/16",  # 6to4
    "3fff::/20",  # documentation
)
# The request target's host: a DNS name in ASCII (Chromium sends IDNA's xn-- form), labels of 1 to
# 63 characters, or an IPv6 literal in brackets. Anything else (user info, a percent sign, a
# space, a non ASCII character, an empty label) is refused before any resolution.
HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_.")
IPV6_CHARS = frozenset("0123456789abcdefABCDEF:.")
TOKEN = frozenset(b"!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
HEX = frozenset(b"0123456789abcdefABCDEF")
# One request per proxy connection for plain http: a kept alive proxy connection would carry
# the next request, to another host, to the first one. `Connection: close` goes both ways and,
# whatever either side does, no byte the client sends after the first request is forwarded.
# Host is written again from the target the proxy checked, never the client's.
HOP_BY_HOP = frozenset(
    {
        b"connection",
        b"host",
        b"keep-alive",
        b"proxy-authorization",
        b"proxy-connection",
        b"upgrade",
    }
)
TRAILERS_MAX = 64


def address_allowed(address: str) -> bool:
    """True for a public unicast address only: unparsable, scoped or special purpose is False."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id is not None:  # fe80::1%eth0: one interface's own link
            return False
        if ip.ipv4_mapped is None:
            return (
                ip in GLOBAL_V6
                and not any(ip in network for network in REFUSED_V6)
                and ip.is_global
            )
        ip = ip.ipv4_mapped
    return not any(ip in network for network in REFUSED_V4) and ip.is_global


def host_name_ok(host: str) -> bool:
    name = host[:-1] if host.endswith(".") else host  # one final dot: a fully qualified name
    if not 0 < len(name) <= 253:
        return False
    return all(0 < len(label) <= 63 and set(label) <= HOST_CHARS for label in name.split("."))


def parse_authority(authority: str, port_required: bool) -> tuple[str, int, str] | None:
    """(host, port, the Host header to send) of a request target's authority, or None when it is
    not a plain host and port. The port is 1 to 65535 in ASCII digits; CONNECT must name it."""
    if not authority.isascii():
        return None
    if authority.startswith("["):  # an IPv6 literal
        literal, bracket, rest = authority[1:].partition("]")
        if not bracket or not literal or not set(literal) <= IPV6_CHARS:
            return None
        try:
            host = ipaddress.IPv6Address(literal).compressed
        except ValueError:
            return None
        shown = f"[{host}]"
    else:
        name, colon, port_text = authority.partition(":")
        host = shown = name.lower()
        rest = colon + port_text
        if not host_name_ok(host):
            return None
    if not rest:
        return None if port_required else (host, 80, shown)
    digits = rest[1:]
    if not (rest[0] == ":" and 0 < len(digits) <= 5 and digits.isdigit()):
        return None
    port = int(digits)
    if not 0 < port < 65536:
        return None
    return host, port, f"{shown}:{port}"


class Head(NamedTuple):
    method: bytes
    host: str
    port: int
    host_header: str
    path: str | None  # None for CONNECT
    version: bytes
    fields: list[tuple[bytes, bytes]]  # (lower case name, the whole line)
    length: int | None  # the body's Content-Length, None when chunked


def parse_head(head: bytes) -> Head | None:
    """The one request of a connection, or None when anything in it is not plain HTTP/1.x: the
    caller answers 400 and forwards nothing. A body is framed one way only (Content-Length, or
    Transfer-Encoding exactly `chunked`), so the proxy and the upstream cannot disagree on where
    it ends."""
    lines = head[:-4].split(b"\r\n")
    if any(b"\r" in line or b"\n" in line or b"\0" in line for line in lines):
        return None
    parts = lines[0].split(b" ")
    if len(parts) != 3:
        return None
    method, target, version = parts
    if not method or not set(method) <= TOKEN or version not in (b"HTTP/1.1", b"HTTP/1.0"):
        return None
    if not target.isascii():
        return None
    text = target.decode("ascii")
    if method == b"CONNECT":
        authority, path = text, None
    elif text.startswith("http://"):
        authority, slash, tail = text[7:].partition("/")
        path = slash + tail or "/"
    else:
        return None
    parsed = parse_authority(authority, port_required=path is None)
    if parsed is None:
        return None
    fields = []
    for line in lines[1:]:
        name, colon, _ = line.partition(b":")
        # A name with a space (`Host :`) or a folded line (leading space) is refused.
        if not colon or not name or not set(name) <= TOKEN:
            return None
        fields.append((name.lower(), line))
    values = {
        name: [line.partition(b":")[2].strip(b" \t") for n, line in fields if n == name]
        for name in (b"transfer-encoding", b"content-length")
    }
    encodings, lengths = values[b"transfer-encoding"], values[b"content-length"]
    if encodings:
        if lengths or encodings != [encodings[0]] or encodings[0].lower() != b"chunked":
            return None
        length = None
    elif lengths:
        if len(set(lengths)) != 1 or not (0 < len(lengths[0]) <= 15 and lengths[0].isdigit()):
            return None
        length = int(lengths[0])
    else:
        length = 0
    return Head(method, *parsed, path, version, fields, length)


async def resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return list(dict.fromkeys(info[4][0] for info in infos))


class Proxy:
    def __init__(self, allow_private: frozenset[str] = frozenset(), resolver=resolve) -> None:
        self.allow_private, self.resolver = allow_private, resolver
        self.refused = self.open = 0
        self.connections_max, self.idle_s, self.ports = CONNECTIONS_MAX, IDLE_S, PORTS

    async def target(self, host: str, port: int) -> str | None:
        """The address to connect to, or None when the destination is refused. The port set
        holds for the allowed test name too, so the test stack matches production."""
        if port not in self.ports:
            return None
        try:
            addresses = await self.resolver(host, port)
        except (OSError, UnicodeError):  # UnicodeError: a label IDNA refuses
            return None
        if not addresses:
            return None
        if host in self.allow_private:
            return addresses[0]
        if not all(address_allowed(a) for a in addresses):
            return None
        return addresses[0]

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One client connection. Whatever happens in it, both sockets are closed at the end."""
        if self.open >= self.connections_max:
            with contextlib.suppress(Exception):
                await self._refuse(writer, 503)
            return
        self.open += 1
        opened = [writer]
        try:
            await self._serve(reader, writer, opened)
        except Exception:
            log.info("dropped a proxy connection on an error")
        finally:
            self.open -= 1
            for w in opened:
                with contextlib.suppress(Exception):
                    w.close()

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, opened: list
    ) -> None:
        """Checks the one request of the connection and relays it. The upstream's writer joins
        `opened`, which handle closes."""
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            return
        request = parse_head(head)
        if request is None:
            return await self._refuse(writer, 400)
        address = await self.target(request.host, request.port)
        if address is None:
            self.refused += 1
            log.info("refused a connection to a non public address")
            return await self._refuse(writer, 403)
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(address, request.port), 15
            )
        except (OSError, TimeoutError):
            return await self._refuse(writer, 502)
        opened.append(up_writer)
        idle = Idle(self.idle_s)
        if request.path is None:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            async with asyncio.TaskGroup() as both:
                both.create_task(pipe(reader, up_writer, idle))
                both.create_task(pipe(up_reader, writer, idle))
            return
        start = b" ".join((request.method, request.path.encode("ascii"), request.version))
        kept = [line for name, line in request.fields if name not in HOP_BY_HOP]
        host = b"Host: " + request.host_header.encode("ascii")
        up_writer.write(b"\r\n".join([start, host, *kept, b"Connection: close", b"", b""]))
        # A failure on either side (a malformed body) cancels the other and closes both.
        async with asyncio.TaskGroup() as both:
            both.create_task(relay_body(reader, up_writer, request.length, idle))
            both.create_task(relay_response(up_reader, writer, idle))

    async def _refuse(self, writer: asyncio.StreamWriter, status: int) -> None:
        reason = {
            400: b"Bad Request",
            403: b"Forbidden",
            502: b"Bad Gateway",
            503: b"Service Unavailable",
        }[status]
        # The reason as the body too: Chromium shows a page with the status (an empty error
        # body becomes its own error, the same for 403 and 502).
        writer.write(
            b"HTTP/1.1 %d %s\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n"
            b"Connection: close\r\n\r\n%s" % (status, reason, len(reason), reason)
        )
        with contextlib.suppress(OSError):
            await writer.drain()
        writer.close()


class Idle:
    """The last byte seen on a connection, in either direction."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.loop = asyncio.get_running_loop()
        self.last = self.loop.time()

    async def read(self, reader: asyncio.StreamReader, size: int, exact: bool = False) -> bytes:
        """Reads like reader.read (or readexactly); b"" once the connection has been idle."""
        while True:
            try:
                data = await asyncio.wait_for(
                    reader.readexactly(size) if exact else reader.read(size),
                    self.seconds,
                )
            except TimeoutError:
                if self.loop.time() - self.last >= self.seconds:
                    return b""
                continue
            self.last = self.loop.time()
            return data

    async def line(self, reader: asyncio.StreamReader) -> bytes:
        data = await asyncio.wait_for(reader.readuntil(b"\r\n"), self.seconds)
        self.last = self.loop.time()
        return data


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, idle: Idle) -> None:
    try:
        while data := await idle.read(reader, 65536):
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.IncompleteReadError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def copy(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, left: int, idle: Idle
) -> None:
    """Exactly `left` bytes, in pieces: a large length never sits in memory whole."""
    while left > 0:
        data = await idle.read(reader, min(left, 65536))
        if not data:
            raise ConnectionError("the client stopped within the body")
        writer.write(data)
        await writer.drain()
        left -= len(data)


async def relay_body(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    length: int | None,
    idle: Idle,
) -> None:
    """The body of the one request (Content-Length, or chunked when `length` is None), then
    nothing: a second request on the same connection is never read, so never forwarded. Chunk
    sizes are written again from their value (no extension) and trailers are dropped."""
    if length is not None:
        await copy(reader, writer, length, idle)
        return
    while True:
        size_text = (await idle.line(reader))[:-2].split(b";")[0].strip(b" \t")
        if not (0 < len(size_text) <= 16 and set(size_text) <= HEX):
            raise ValueError("bad chunk size")
        size = int(size_text, 16)
        writer.write(b"%x\r\n" % size)
        if size == 0:
            for _ in range(TRAILERS_MAX):
                if await idle.line(reader) == b"\r\n":
                    break
            else:
                raise ValueError("too many trailer lines")
            writer.write(b"\r\n")
            break
        await copy(reader, writer, size, idle)
        if await idle.read(reader, 2, exact=True) != b"\r\n":
            raise ValueError("a chunk does not end with CRLF")
        writer.write(b"\r\n")
    await writer.drain()


async def relay_response(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, idle: Idle
) -> None:
    """The upstream's answer with `Connection: close` in its head, so Chromium sends no second
    request on this connection."""
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), idle.seconds)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
        return
    lines = [
        h
        for h in head.split(b"\r\n")[:-2]
        if h.partition(b":")[0].strip().lower() not in HOP_BY_HOP - {b"host"}
    ]
    writer.write(b"\r\n".join([*lines, b"Connection: close", b"", b""]))
    await pipe(reader, writer, idle)


async def serve(proxy: Proxy, port: int) -> asyncio.Server:
    return await asyncio.start_server(proxy.handle, "127.0.0.1", port, limit=HEAD_MAX)
