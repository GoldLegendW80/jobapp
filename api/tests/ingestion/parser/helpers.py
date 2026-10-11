"""Shared by the tests of the parser service: the two standalone files of the sandbox loaded by
their path (they are no package and import nothing of the application), and a stand in for the
child, so that the server can be run in the test process on any system."""

import asyncio
import hashlib
import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

API = Path(__file__).resolve().parents[3]
ROOT = API.parent
SANDBOX = API / "app" / "ingestion" / "sandbox"
HERE = Path(__file__).resolve().parent
STAND_IN = (sys.executable, str(HERE / "stand_in_child.py"))

_loaded: dict[str, ModuleType] = {}


def sandbox_module(name: str) -> ModuleType:
    """sandbox/main.py or sandbox/server.py as a module, loaded once."""
    if name not in _loaded:
        spec = importlib.util.spec_from_file_location(
            f"parser_sandbox_{name}", SANDBOX / f"{name}.py"
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _loaded[name] = module
    return _loaded[name]


# What a ping child reports when the lockdown is in force, as the service's healthcheck and the
# worker's client both expect it (the kernel release of the Jetson, for the look of it).
def good_sandbox() -> dict:
    server = sandbox_module("server")
    return {
        "seccomp": 2,
        "filters": 2,
        "no_new_privs": 1,
        "dumpable": 0,
        "capabilities": 0,
        "limits": dict(server.EXPECTED_LIMITS),
        "denied": dict.fromkeys(server.REQUIRED_DENIED, "EPERM"),
        "isolated": True,
        "environ": 0,
        "uid": 10002,
        "descriptors": [0, 1, 2],
        "sockets": 0,
        "writable": [],
        "parent_hidden": True,
        "cryptography_blocked": True,
        "machine": "aarch64",
        "kernel": "5.15.148-tegra",
    }


def good_ping(data: bytes = b"") -> dict:
    return {
        "ok": True,
        "kind": "ping",
        "sandbox": good_sandbox(),
        "received": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "usage": {"cpu_ms": 49, "max_rss_kb": 26308},
    }


class BackgroundServer:
    """The server of the service in a thread of its own, for the tests whose client is
    synchronous (the command line) or runs in another event loop."""

    def __init__(self, child: tuple[str, ...] = STAND_IN, **bounds: Any) -> None:
        self.child, self.bounds = child, {"quiet": True, **bounds}
        self.port = 0
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(target=lambda: asyncio.run(self._serve()), daemon=True)

    async def _serve(self) -> None:
        server = sandbox_module("server")
        self.instance = server.Server(self.child, **self.bounds)
        listener = await server.start(self.instance, "127.0.0.1", 0)
        self.port = listener.sockets[0].getsockname()[1]
        self._loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
        self._ready.set()
        await self._stop.wait()
        listener.close()
        await self.instance.close()

    def __enter__(self) -> "BackgroundServer":
        self._thread.start()
        assert self._ready.wait(10), "the server did not start"
        return self

    def stop(self) -> None:
        """Stop the server now, with whatever it was running: its clients see it go."""
        if self._thread.is_alive() and self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
            self._thread.join(15)

    def __exit__(self, *_: object) -> None:
        self.stop()
