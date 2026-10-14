"""Run inside the containers of compose.renderer.yml, standard library only (the renderer's
image has no httpx). Each command prints one line of JSON.

    relay.py render <host>         the HTML on stdin to http://<host>:7071/render; the status,
                                   X-Pages and the body in base64
    relay.py connect <host> <port> a direct TCP connection, as a program inside would open it
    relay.py listen <port>...      the listener: records every connection in /tmp/seen
    relay.py seen                  what the listener recorded
"""

import base64
import http.client
import json
import socket
import sys
import threading
from pathlib import Path

SEEN = Path("/tmp/seen")  # noqa: S108  (the listener's own tmpfs)


def render(host: str) -> dict:
    body = sys.stdin.buffer.read()
    connection = http.client.HTTPConnection(host, 7071, timeout=60)
    connection.request("POST", "/render", body, {"Content-Type": "text/html; charset=utf-8"})
    response = connection.getresponse()
    data = response.read()
    return {
        "status": response.status,
        "pages": response.getheader("X-Pages"),
        "body": base64.b64encode(data).decode(),
    }


def connect(host: str, port: str) -> dict:
    try:
        socket.create_connection((host, int(port)), timeout=5).close()
    except OSError as error:
        return {"ok": False, "error": type(error).__name__}
    return {"ok": True}


def listen(ports: list[str]) -> None:
    lock = threading.Lock()

    def serve(port: int) -> None:
        with socket.create_server(("0.0.0.0", port)) as server:  # noqa: S104  (on `open`)
            while True:
                connection, peer = server.accept()
                with lock, SEEN.open("a") as seen:
                    seen.write(f"{port} {peer[0]}\n")
                connection.close()

    threads = [threading.Thread(target=serve, args=(int(p),), daemon=True) for p in ports]
    for thread in threads:
        thread.start()
    threads[0].join()


if __name__ == "__main__":
    command, *rest = sys.argv[1:]
    if command == "listen":
        listen(rest)
    elif command == "seen":
        print(json.dumps(SEEN.read_text().splitlines() if SEEN.exists() else []))
    else:
        print(json.dumps(render(*rest) if command == "render" else connect(*rest)))
