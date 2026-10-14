"""Shared by the tests of the parser service: the two standalone files of the sandbox loaded by
their path (they are no package and import nothing of the application), and a stand in for the
child, so that the server can be run in the test process on any system."""

import asyncio
import hashlib
import importlib.util
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
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


# The names under which the child imports its own files from beside them (it puts the folder on
# its path): the handlers, and the helper two of them share.
FLAT_NAMES = ("docx", "image", "linkedin_zip", "mail_html", "pdf_render", "pdf_text", "zipcaps")
_ABSENT = object()


@contextmanager
def restoring_modules(*names: str) -> Iterator[None]:
    """Whatever the body does to these entries of sys.modules (imports, replaces, pops), they are
    put back as they were: absent stays absent, a module that was there is there again."""
    saved = {name: sys.modules.get(name, _ABSENT) for name in names}
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is _ABSENT:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


@contextmanager
def as_in_the_child() -> Iterator[None]:
    """The world a handler runs in inside the sandbox: the folder of the handlers on the path, so
    that `import zipcaps` or `import pdf_text` (pdf_render.py imports it when it opens a PDF) find
    the file beside them, and `pdf_text` is the very module the tests of the reader use. Wrap
    every call of a real handler in it, and its loading: nothing of it outlives the block, so what
    another test imported or popped before cannot change the result."""
    with restoring_modules(*FLAT_NAMES):
        sys.path.insert(0, str(SANDBOX))
        try:
            sys.modules["pdf_text"] = sandbox_module("pdf_text")
            yield
        finally:
            sys.path.remove(str(SANDBOX))


def sandbox_handler(name: str) -> ModuleType:
    """A handler of sandbox/ that imports a helper from beside it (docx.py and linkedin_zip.py
    import zipcaps.py), loaded as main.py loads it: with that directory on the path."""
    if name not in _loaded:
        with as_in_the_child():
            sandbox_module(name)
    return _loaded[name]


class Plenty:
    """A budget that is never spent."""

    def spent(self) -> bool:
        return False


class SpentAfter:
    """A budget that is spent from its `calls`th question on."""

    def __init__(self, calls: int) -> None:
        self.left = calls

    def spent(self) -> bool:
        self.left -= 1
        return self.left < 0
