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
# One request per upstream connection for plain http: a kept alive proxy connection would carry
# the next request, to another host, to the first one.
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
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
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
        self.refused = 0

    async def target(self, host: str, port: int) -> str | None:
        """The address to connect to, or None when the destination is refused."""
        host = host.strip("[]").lower()
        allowed_name = host in self.allow_private
        if not allowed_name and port not in PORTS:
            return None
        try:
            addresses = await self.resolver(host, port)
        except OSError:
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
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            writer.close()
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
        if not port.isdigit():
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
        if path is None:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            headers = [
                h for h in rest.split(b"\r\n") if not h.lower().startswith(DROP_HEADERS)
            ]
            up_writer.write(
                b" ".join((method, path.encode("latin-1"), version)) + b"\r\n"
            )
            up_writer.write(
                b"\r\n".join([*headers[:-2], b"Connection: close", b"", b""])
            )
        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))

    async def _refuse(self, writer: asyncio.StreamWriter, status: int) -> None:
        reason = {400: b"Bad Request", 403: b"Forbidden", 502: b"Bad Gateway"}[status]
        writer.write(
            b"HTTP/1.1 %d %s\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            % (status, reason)
        )
        with contextlib.suppress(OSError):
            await writer.drain()
        writer.close()


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            writer.close()


async def serve(proxy: Proxy, port: int) -> asyncio.Server:
    return await asyncio.start_server(proxy.handle, "127.0.0.1", port, limit=HEAD_MAX)
