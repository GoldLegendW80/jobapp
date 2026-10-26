"""What the tests of A1 share: the fake ATS forms server run in process, and a small client for
it (urllib, no browser). Every address is the server's own on a loopback port; nothing here
reaches a real job site."""

from __future__ import annotations

import base64
import importlib.util
import json
import re
import sys
import threading
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

STUB_PATH = Path(__file__).resolve().parents[3] / "tests" / "ats_forms_stub.py"
PDF = b"%PDF-1.7\n" + bytes(range(256)) + b"\n%%EOF\n"


def load_stub() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ats_forms_stub", STUB_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


@dataclass
class Reply:
    status: int
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")


def multipart(
    fields: dict[str, Any], files: dict[str, tuple[str, bytes]] | None = None
) -> tuple[bytes, str]:
    """A multipart/form-data body: a text field (a list repeats it) and files by field name."""
    boundary = f"----a1-{uuid.uuid4().hex}"
    out = b""
    for name, value in fields.items():
        for one in value if isinstance(value, list) else [value]:
            out += (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{one}\r\n'
            ).encode()
    for name, (filename, data) in (files or {}).items():
        out += (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n'
            ).encode()
            + data
            + b"\r\n"
        )
    return out + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


class Forms:
    """A running fake ATS forms server and its client."""

    def __init__(self, base: str, stub: Any, module: ModuleType) -> None:
        self.base = base
        self.stub = stub
        self.module = module

    def url(self, path: str) -> str:
        return self.base + path

    def get(self, path: str) -> Reply:
        return self._call(urllib.request.Request(self.url(path)))  # noqa: S310

    def json(self, path: str, payload: Any) -> Reply:
        return self._call(
            urllib.request.Request(  # noqa: S310
                self.url(path),
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
        )

    def session(self, path: str) -> str:
        found = re.search(r'data-session="([^"]+)"', self.get(path).text)
        return found.group(1) if found else ""

    def observe(self, token: str, report: dict[str, Any]) -> Reply:
        return self.json(f"/_stub/behaviour/{token}", report)

    def post(
        self,
        path: str,
        fields: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes]] | None = None,
        *,
        dwell_ms: int = 5000,
        webdriver: bool = False,
        behaviour: bool = True,
    ) -> Reply:
        fields = dict(fields or {})
        if path.startswith("/ashby/") and behaviour:
            assert isinstance(self.stub.clock, Clock), "paced test posts need an injected clock"
            token = self.session(path)
            if token:
                assert self.observe(token, {"event": "environment", "webdriver": webdriver}).json()[
                    "ok"
                ]
                assert self.observe(token, {"event": "input"}).json()["ok"]
                self.stub.clock.advance(dwell_ms / 1000)
                fields["_session"] = token
        return self.json(
            path,
            {
                "fields": fields,
                "files": [
                    {
                        "field": name,
                        "filename": filename,
                        "content_type": "application/pdf",
                        "data": base64.b64encode(data).decode(),
                    }
                    for name, (filename, data) in (files or {}).items()
                ],
            },
        )

    def _call(self, request: urllib.request.Request) -> Reply:
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return Reply(response.status, response.read())
        except urllib.error.HTTPError as problem:
            return Reply(problem.code, problem.read())

    def submissions(self) -> list[dict[str, Any]]:
        return self.get("/_stub/submissions").json()

    def reset(self) -> None:
        self.post("/_stub/reset")

    def posting_path(self, ats: str, variant: str = "") -> str:
        for row in self.get("/_stub/postings").json():
            if row["ats"] == ats and row["variant"] == variant:
                return row["path"]
        raise KeyError((ats, variant))


class Clock:
    """An injected monotonic test clock; the HTTP client never claims the elapsed duration."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@contextmanager
def running(**options: Any) -> Iterator[Forms]:
    module = load_stub()
    server, stub = module.make_server("127.0.0.1", 0, env={}, **options)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield Forms(f"http://127.0.0.1:{server.server_address[1]}", stub, module)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# The fields that make a good Ashby application, by name, with a paced dwell.
ASHBY_GOOD: dict[str, Any] = {
    "_systemfield_name": "Alex Example",
    "_systemfield_email": "e2e-alex@example.org",
    "_systemfield_location": "Paris, France",
    "q_eu_work": "Yes",
    "q_sponsor": "No",
    "q_why": "I build agents that run operations.",
    "consent": "on",
}
ASHBY_FILES = {"_systemfield_resume": ("cv.pdf", PDF)}
GH_GOOD: dict[str, Any] = {
    "first_name": "Alex",
    "last_name": "Example",
    "email": "e2e-alex@example.org",
    "question_1001": "Yes",
    "question_1002": "France",
    "privacy": "on",
}
GH_FILES = {"resume": ("cv.pdf", PDF)}
