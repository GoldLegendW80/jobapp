"""The real server process: uvicorn with the factory, as the Dockerfile runs it."""

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

from app import main
from app.config import ConfigError
from tests.conftest import free_port

API_DIR = Path(__file__).resolve().parents[1]


def uvicorn(env: dict[str, str], port: int) -> subprocess.Popen[str]:
    clean = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "LOG_LEVEL")}
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
        cwd=API_DIR, env={**clean, **env}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )  # fmt: skip


# A bound that only turns a process that hangs into a failure: a gate on a loaded machine took
# more than 15 s to import the app (issue #104).
HANG_S = 180


def test_missing_database_url_exits_fast_with_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fast: the settings are refused before anything that could wait (the engine) is built.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    built: list[object] = []
    monkeypatch.setattr(main, "build_engine", built.append)
    with pytest.raises(ConfigError, match="DATABASE_URL is not set"):
        main.create_app()
    assert built == []
    # And the real process, started as the Dockerfile starts it, ends by itself with that error.
    proc = uvicorn({}, free_port())
    out, _ = proc.communicate(timeout=HANG_S)
    assert proc.returncode != 0
    assert "Invalid configuration: DATABASE_URL is not set" in out


def test_server_serves_health(database_url: str) -> None:
    port = free_port()
    proc = uvicorn({"DATABASE_URL": database_url}, port)
    try:
        deadline = time.monotonic() + HANG_S
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                    assert response.status == 200
                    assert response.read() == b'{"status":"ok"}'
                break
            except OSError:
                assert proc.poll() is None, proc.stdout.read() if proc.stdout else ""
                assert time.monotonic() < deadline
                time.sleep(0.2)
    finally:
        proc.terminate()
        out, _ = proc.communicate(timeout=HANG_S)
    assert '"msg": "api starting"' in out
    assert '"msg": "api stopped"' in out
