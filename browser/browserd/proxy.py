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

The test stack names its pages server in BROWSER_PROXY_ALLOW_PRIVATE (a private Docker address);
compose.yml never sets it.
"""

import asyncio
import contextlib
import ipaddress
import logging
import socket

log = logging.getLogger("browserd.proxy")

PORTS = frozenset({80, 443, 8080, 8443})
HEAD_MAX = 64 * 1024
# Chromium opens at most 32 sockets to one proxy for ordinary requests and up to 255 WebSockets
# (tunnels here), so 512 open connections never constrain a page and bound what one can hold.
CONNECTIONS_MAX = 512
# A connection with no byte in either direction for this long is closed: a page cannot keep idle
# tunnels open for ever. Five minutes, the lease's own idle timeout (BROWSER_IDLE_S).
IDLE_S = 300.0
# IPv6 prefixes that carry an IPv4 address (IPv4 compatible, NAT64 and its local use prefix, 6to4,
# Teredo): a gateway on the way may turn them into the IPv4 inside, private or not, so they are
# refused outright. IPv4 mapped addresses (::ffff:0:0/96) are checked as the IPv4 they map.
EMBEDDED_IPV4 = tuple(
    ipaddress.ip_network(n)
    for n in ("::/96", "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)
# One request per proxy connection for plain http: a kept alive proxy connection would carry
# the next request, to another host, to the first one. `Connection: close` goes both ways and,
# whatever either side does, no byte the client sends after the first request is forwarded.
DROP_HEADERS = (
    b"proxy-connection:",
    b"proxy-authorization:",
    b"connection:",
    b"keep-alive:",
)


def split_authority(authority: str) -> tuple[str, str]:
    if authority.startswith("["):  # an IPv6 literal
        host, _, port = authority[1:].partition("]")
        return host, port[1:] or "80"
    host, _, port = authority.partition(":")
    return host, port or "80"


def address_allowed(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif any(ip in network for network in EMBEDDED_IPV4):
            return False
    return ip.is_global and not ip.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return list(dict.fromkeys(info[4][0] for info in infos))


class Proxy:
    def __init__(
        self, allow_private: frozenset[str] = frozenset(), resolver=resolve
    ) -> None:
        self.allow_private, self.resolver = allow_private, resolver
        self.refused = self.open = 0
        self.connections_max, self.idle_s = CONNECTIONS_MAX, IDLE_S

    async def target(self, host: str, port: int) -> str | None:
        """The address to connect to, or None when the destination is refused."""
        host = host.strip("[]").lower()
        allowed_name = host in self.allow_private
        if not allowed_name and port not in PORTS:
            return None
        try:
            addresses = await self.resolver(host, port)
        except (
            OSError,
            UnicodeError,
        ):  # UnicodeError: a label IDNA refuses (over 63 characters)
            return None
        if not addresses:
            return None
        if allowed_name:
            return addresses[0]
        if not all(address_allowed(a) for a in addresses):
            return None
        return addresses[0]

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """One client connection. Whatever happens in it, both sockets are closed at the end."""
        if self.open >= self.connections_max:
            with contextlib.suppress(Exception):
                await self._refuse(writer, 503)
            return
        self.open += 1
        opened = [writer]
        try:
            await self._serve(reader, writer, opened)
        except Exception:  # noqa: BLE001 - any input: answer nothing more and close
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
        line, _, rest = head.partition(b"\r\n")
        parts = line.split(b" ")
        if len(parts) != 3:
            return await self._refuse(writer, 400)
        method, target, version = parts
        if method == b"CONNECT":
            authority, path = target.decode("latin-1"), None
        elif target.startswith(b"http://"):
            authority, slash, tail = target.decode("latin-1")[7:].partition("/")
            path = slash + tail or "/"
        else:
            return await self._refuse(writer, 400)
        host, port = split_authority(authority)
        # isascii: "²".isdigit() is True and int() refuses it.
        if not (port.isascii() and port.isdigit() and 0 < int(port) < 65536):
            return await self._refuse(writer, 400)
        address = await self.target(host, int(port))
        if address is None:
            self.refused += 1
            log.info("refused a connection to a non public address")
            return await self._refuse(writer, 403)
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(address, int(port)), 15
            )
        except (OSError, TimeoutError):
            return await self._refuse(writer, 502)
        opened.append(up_writer)
        idle = Idle(self.idle_s)
        if path is None:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            async with asyncio.TaskGroup() as both:
                both.create_task(pipe(reader, up_writer, idle))
                both.create_task(pipe(up_reader, writer, idle))
            return
        headers = [
            h
            for h in rest.split(b"\r\n")[:-2]
            if not h.lower().startswith(DROP_HEADERS)
        ]
        up_writer.write(b" ".join((method, path.encode("latin-1"), version)) + b"\r\n")
        up_writer.write(b"\r\n".join([*headers, b"Connection: close", b"", b""]))
        # A failure on either side (a malformed body) cancels the other and closes both.
        async with asyncio.TaskGroup() as both:
            both.create_task(relay_body(reader, up_writer, headers, idle))
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

    async def read(
        self, reader: asyncio.StreamReader, size: int, exact: bool = False
    ) -> bytes:
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


async def pipe(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, idle: Idle
) -> None:
    try:
        while data := await idle.read(reader, 65536):
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.IncompleteReadError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def relay_body(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    headers: list[bytes],
    idle: Idle,
) -> None:
    """The body of the one request (Content-Length or chunked), then nothing: a second request
    on the same connection is never read, so never forwarded."""
    fields = {
        name.strip().lower(): value.strip()
        for name, _, value in (h.partition(b":") for h in headers)
    }
    if b"chunked" in fields.get(b"transfer-encoding", b"").lower():
        while True:
            size_line = await idle.line(reader)
            writer.write(size_line)
            size = int(size_line.split(b";")[0].strip(), 16)
            if size == 0:
                while (trailer := await idle.line(reader)) != b"\r\n":
                    writer.write(trailer)
                writer.write(b"\r\n")
                break
            writer.write(await idle.read(reader, size + 2, exact=True))
            await writer.drain()
    else:
        left = int(fields.get(b"content-length", b"0") or 0)
        while left > 0:
            data = await idle.read(reader, min(left, 65536))
            if not data:
                break
            writer.write(data)
            await writer.drain()
            left -= len(data)
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
        h for h in head.split(b"\r\n")[:-2] if not h.lower().startswith(DROP_HEADERS)
    ]
    writer.write(b"\r\n".join([*lines, b"Connection: close", b"", b""]))
    await pipe(reader, writer, idle)


async def serve(proxy: Proxy, port: int) -> asyncio.Server:
    return await asyncio.start_server(proxy.handle, "127.0.0.1", port, limit=HEAD_MAX)
