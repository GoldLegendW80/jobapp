"""The hands of test_container.py inside its stack (compose.sandbox.yml). Test only.

It runs in the `client` container, which stands where the worker stands (the `default` and
`parsing` networks, uid 10001, the application's code), and in `peers`, which stands for db,
redis and the api. Every command prints one line of JSON and exits 0: the test judges.

    driver.py listen PORT...                 answer on these ports for ever (peers)
    driver.py reach HOST PORT                resolve HOST, then connect: what happened
    driver.py raw HOST KIND OPTIONS [SIZE]   one job, the answer as it came (header and body)
    driver.py parse HOST KIND OPTIONS [SIZE] one job through app.ingestion.parser_client
    driver.py ping HOST [SIZE]               a ping through the client
    driver.py during HOST SECONDS            pings while a job of SECONDS runs
    driver.py together HOST SECONDS COUNT    COUNT jobs of SECONDS sent at once
    driver.py clumsy HOST WHAT [ping]        a client that breaks the protocol, then a job
                                             (or a ping, where the job slot is taken)
"""

import asyncio
import contextlib
import json
import socket
import sys
import time

PORT = 7070
MIB = 1024 * 1024


def line_of(kind: str, size: int, options: dict) -> bytes:
    return json.dumps({"kind": kind, "size": size, "options": options}).encode() + b"\n"


async def exchange(host: str, payload: bytes, patience: float = 100.0) -> dict:
    """Send `payload`, read an answer to the end of the stream: header, body, what followed."""
    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, PORT), 5)
    except (OSError, TimeoutError) as exc:
        return {"error": type(exc).__name__}
    try:
        with contextlib.suppress(ConnectionError):
            writer.write(payload)
            await writer.drain()
        async with asyncio.timeout(patience):
            line = await reader.readline()
            if not line:
                return {"closed": True, "ms": int((time.monotonic() - started) * 1000)}
            header = json.loads(line)
            raw = await reader.readexactly(header["length"])
            rest = await reader.read()
    except (ConnectionError, asyncio.IncompleteReadError, TimeoutError) as exc:
        return {"error": type(exc).__name__, "ms": int((time.monotonic() - started) * 1000)}
    finally:
        writer.close()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = "not json"
    return {
        "header": header,
        "body": body,
        "body_bytes": len(raw),
        "rest": len(rest),
        "ms": int((time.monotonic() - started) * 1000),
    }


async def raw(host: str, kind: str, options: dict, size: int = 0) -> dict:
    return await exchange(host, line_of(kind, size, options) + bytes(size))


def client():
    sys.path.insert(0, "/app")
    from app.ingestion import parser_client

    return parser_client


async def parse(host: str, kind: str, options: dict, size: int = 0) -> dict:
    pc = client()
    started = time.monotonic()
    try:
        parsed = await pc.parse(kind, bytes(size), options, host=host)
    except pc.ParserError as error:
        found = {
            "error": type(error).__name__,
            "code": error.code,
            "failure": error.failure,
            "values": dict(error.values),
        }
    else:
        found = {
            "parsed": {
                "text_chars": None if parsed.text is None else len(parsed.text),
                "text_start": None if parsed.text is None else parsed.text[:40],
                "pages": parsed.pages,
                "notes": [[note.code, dict(note.values)] for note in parsed.notes],
                "images": len(parsed.images),
            }
        }
    return {**found, "ms": int((time.monotonic() - started) * 1000)}


async def ping(host: str, size: int = 0) -> dict:
    pc = client()
    started = time.monotonic()
    try:
        report = await pc.ping(bytes(size), host=host, timeout_s=90)
    except pc.ParserError as error:
        return {"error": type(error).__name__, "code": error.code}
    return {
        "in_force": report.in_force,
        "problems": list(report.problems),
        "received": report.received,
        "sandbox": dict(report.sandbox),
        "cpu_ms": report.cpu_ms,
        "max_rss_kb": report.max_rss_kb,
        "ms": int((time.monotonic() - started) * 1000),
    }


async def during(host: str, seconds: float) -> dict:
    """A job of `seconds`, and pings sent while it runs: how long each took, and whether the
    job was still running when the last one came back."""
    job = asyncio.ensure_future(raw(host, "pdf_render", {"act": "sleep", "s": seconds}, 1))
    await asyncio.sleep(1.0)
    pings = []
    for _ in range(5):
        answer = await raw(host, "ping", {})
        pings.append({"ms": answer.get("ms"), "ok": bool(answer.get("header", {}).get("ok"))})
        await asyncio.sleep(0.2)
    running = not job.done()
    return {"pings": pings, "job_still_running": running, "job": await job}


async def together(host: str, seconds: float, count: int) -> dict:
    """`count` jobs of `seconds` each, sent at the same moment: when each one came back."""
    started = time.monotonic()

    async def one() -> dict:
        answer = await raw(host, "pdf_render", {"act": "sleep", "s": seconds}, 1)
        return {"ok": answer.get("header", {}).get("code"), "at": time.monotonic() - started}

    jobs = await asyncio.gather(*(one() for _ in range(count)))
    return {"jobs": sorted(jobs, key=lambda job: job["at"])}


async def clumsy(host: str, what: str, then: str = "job") -> dict:
    """A client that breaks the protocol in one way, then an ordinary job: the service must
    answer the first as it says and serve the second. `then` "ping": a ping instead of the
    job, for an instance whose one job slot is taken on purpose."""
    if what == "header_5k":
        first = await exchange(host, line_of("docx", 0, {"pad": "x" * 5000}))
    elif what == "size_over":
        # The header alone: a refusal that comes proves no byte of a document was waited for.
        first = await exchange(host, line_of("docx", 20 * MIB + 1, {}), patience=5)
    elif what == "size_over_sending":
        first = await exchange(host, line_of("docx", 20 * MIB + 1, {}) + bytes(8 * MIB))
    elif what == "silent":
        first = await exchange(host, b"", patience=30)
    elif what == "half_a_document":
        first = await exchange(host, line_of("docx", 1000, {}) + b"x" * 10, patience=30)
    elif what == "closes_mid_document":
        _, writer = await asyncio.open_connection(host, PORT)
        writer.write(line_of("docx", 1000, {}) + b"x" * 10)
        await writer.drain()
        writer.close()
        first = {"closed_by_client": True}
    elif what == "leaves_during_job":
        _, writer = await asyncio.open_connection(host, PORT)
        writer.write(line_of("pdf_render", 1, {"act": "sleep", "s": 50}) + b"x")
        await writer.drain()
        await asyncio.sleep(2)
        writer.close()
        first = {"closed_by_client": True}
    elif what == "unknown_kind":
        first = await exchange(host, line_of("rtf", 0, {}))
    elif what == "not_json":
        first = await exchange(host, b"GET / HTTP/1.1\r\nHost: parser\r\n\r\n")
    else:
        raise SystemExit(f"unknown: {what}")
    started = time.monotonic()
    if then == "ping":
        after = await raw(host, "ping", {})
    else:
        after = await raw(host, "pdf_render", {"act": "echo"}, 3)
    return {"first": first, "after": after, "after_ms": int((time.monotonic() - started) * 1000)}


def reach(host: str, port: int) -> dict:
    """Resolve, then connect: the two things a process needs to get anywhere."""
    found: dict = {"resolved": None, "connected": False}
    try:
        found["resolved"] = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0][4][0]
    except OSError as exc:
        found["resolve_error"] = type(exc).__name__
        return found
    try:
        with socket.create_connection((found["resolved"], port), timeout=4):
            found["connected"] = True
    except OSError as exc:
        found["connect_error"] = type(exc).__name__
    return found


async def listen(ports: list[int]) -> None:
    async def greet(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"peer\n")
        writer.close()

    servers = [await asyncio.start_server(greet, "0.0.0.0", port) for port in ports]  # noqa: S104
    await asyncio.gather(*(server.serve_forever() for server in servers))


def main(argv: list[str]) -> int:
    command, arguments = argv[1], argv[2:]
    if command == "listen":
        asyncio.run(listen([int(port) for port in arguments]))
        return 0
    if command == "reach":
        found = reach(arguments[0], int(arguments[1]))
    elif command in ("raw", "parse"):
        host, kind, options = arguments[0], arguments[1], json.loads(arguments[2])
        size = int(arguments[3]) if len(arguments) > 3 else 0
        found = asyncio.run((raw if command == "raw" else parse)(host, kind, options, size))
    elif command == "ping":
        found = asyncio.run(ping(arguments[0], int(arguments[1]) if len(arguments) > 1 else 0))
    elif command == "during":
        found = asyncio.run(during(arguments[0], float(arguments[1])))
    elif command == "together":
        found = asyncio.run(together(arguments[0], float(arguments[1]), int(arguments[2])))
    elif command == "clumsy":
        found = asyncio.run(clumsy(*arguments[:3]))
    else:
        raise SystemExit(__doc__)
    print(json.dumps(found))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
