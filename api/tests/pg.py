"""Throwaway Postgres containers for the integration tests."""

import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass

import psycopg

IMAGE = "postgres:16-alpine"
USER = "jobapp"
PASSWORD = "test-pässword"  # non-ASCII on purpose: the URL must be encoded correctly
DATABASE = "jobapp_test"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@dataclass
class Postgres:
    name: str
    port: int

    def url(self, user: str = USER, password: str = PASSWORD, database: str = DATABASE) -> str:
        from urllib.parse import quote

        return (
            f"postgresql+psycopg://{quote(user, safe='')}:{quote(password, safe='')}"
            f"@127.0.0.1:{self.port}/{database}"
        )

    def wait_ready(self, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                with psycopg.connect(
                    host="127.0.0.1",
                    port=self.port,
                    user=USER,
                    password=PASSWORD,
                    dbname=DATABASE,
                    connect_timeout=2,
                ) as conn:
                    conn.execute("select 1")
                return
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)

    def stop(self) -> None:
        docker("stop", "-t", "1", self.name)

    def start(self) -> None:
        docker("start", self.name)
        self.port = _host_port(self.name)
        self.wait_ready()

    def pause(self) -> None:
        docker("pause", self.name)

    def unpause(self) -> None:
        docker("unpause", self.name)

    def remove(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, check=False)


def _host_port(name: str, timeout: float = 10) -> int:
    # Right after `docker run -d` (or `start`) the mapping can still be empty for a moment.
    deadline = time.monotonic() + timeout
    while True:
        lines = docker("port", name, "5432/tcp").splitlines()
        if lines:
            return int(lines[0].rsplit(":", 1)[1])
        if time.monotonic() > deadline:
            raise RuntimeError(f"container {name} publishes no port for 5432")
        time.sleep(0.2)


def start_postgres(port: int | None = None) -> Postgres:
    name = f"jobapp-test-pg-{uuid.uuid4().hex[:8]}"
    publish = f"127.0.0.1:{port}:5432" if port else "127.0.0.1::5432"
    docker(
        "run", "-d", "--name", name,
        "-e", f"POSTGRES_USER={USER}",
        "-e", f"POSTGRES_PASSWORD={PASSWORD}",
        "-e", f"POSTGRES_DB={DATABASE}",
        "-p", publish,
        IMAGE,
    )  # fmt: skip
    pg = Postgres(name=name, port=_host_port(name))
    try:
        pg.wait_ready()
    except Exception:
        pg.remove()
        raise
    return pg
