"""A door for the tests into the `parsing` network (compose.pdf.yml). Test only.

The parser is on an internal network that only the worker reaches, and an internal network
publishes no port. This process stands where the worker stands, on `default` and on `parsing`,
and passes bytes both ways without looking at them: the test process then talks to the real
parser with the application's own client, app/ingestion/parser_client.py, unchanged.

    relay.py 7071=parser 7072=parser-slow ...     listen on each port, forward to that host:7070
    relay.py --check 7071                         exit 0 when the port listens (the healthcheck)
"""

import asyncio
import socket
import sys

PARSER_PORT = 7070


async def pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        pass


def door(host: str):
    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            far_reader, far_writer = await asyncio.wait_for(
                asyncio.open_connection(host, PARSER_PORT), 5
            )
        except (OSError, TimeoutError):
            writer.close()  # nobody there: the client sees the connection end without a word
            return
        try:
            await asyncio.gather(pump(reader, far_writer), pump(far_reader, writer))
        finally:
            far_writer.close()
            writer.close()

    return serve


async def main(doors: dict[int, str]) -> None:
    servers = [
        await asyncio.start_server(door(host), "0.0.0.0", port)  # noqa: S104, inside its container
        for port, host in doors.items()
    ]
    await asyncio.gather(*(server.serve_forever() for server in servers))


if __name__ == "__main__":
    if sys.argv[1] == "--check":
        with socket.create_connection(("127.0.0.1", int(sys.argv[2])), timeout=3):
            sys.exit(0)
    asyncio.run(main({int(port): host for port, host in (arg.split("=") for arg in sys.argv[1:])}))
