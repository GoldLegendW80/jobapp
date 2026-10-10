"""A throwaway Redis 7 container for the queue tests (same image as compose.yml)."""

import socket
import time
import uuid
from dataclasses import dataclass

from tests.pg import docker

IMAGE = "redis:7.4.11-alpine"


@dataclass
class Redis:
    name: str
    port: int

    @property
    def url(self) -> str:
        return f"redis://127.0.0.1:{self.port}/0"

    def wait_ready(self, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1) as sock:
                    sock.sendall(b"PING\r\n")
                    if sock.recv(16).startswith(b"+PONG"):
                        return
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("redis did not answer PING")
            time.sleep(0.2)

    def remove(self) -> None:
        docker("rm", "-f", "-v", self.name)


def start_redis() -> Redis:
    name = f"jobapp-test-redis-{uuid.uuid4().hex[:8]}"
    docker("run", "-d", "--name", name, "-p", "127.0.0.1::6379", IMAGE)
    deadline = time.monotonic() + 10
    while True:
        lines = docker("port", name, "6379/tcp").splitlines()
        if lines:
            break
        if time.monotonic() > deadline:
            docker("rm", "-f", "-v", name)
            raise RuntimeError("redis container publishes no port")
        time.sleep(0.2)
    server = Redis(name=name, port=int(lines[0].rsplit(":", 1)[1]))
    try:
        server.wait_ready()
    except Exception:
        server.remove()
        raise
    return server
