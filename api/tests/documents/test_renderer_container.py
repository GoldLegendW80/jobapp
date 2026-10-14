"""The renderer service for real: its image, built from renderer/, started by Compose from the
service compose.yml defines (renderer/compose.renderer.yml), with Chromium inside.

What runs: D1's fixtures through the real engine and the worker's own checks (the page count
PDFium reads, the font check), the fit loop on the fixture that needs the tightest step, an HTML
of 1 MB, a missing font that fails loudly, the container as Compose starts it (uid, read only
root, capabilities, limits, no environment of the application), and the egress test, one layer
at a time: with the network open, every outside address the HTML asks for is refused by the
request interception alone; with the interception out of the way, a direct connection from
inside the container fails on the internal network alone.

Marked `docker`: it needs a Docker daemon. The image is built by the fixture under a tag of its
own, unless RENDERER_TEST_IMAGE names one; a first build downloads Chromium (a few minutes).
"""

import asyncio
import base64
import json
import os
import secrets
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pytest

from app.documents import fit, render_client
from app.documents.render_client import Rendered, RenderError
from tests.documents import render_html
from tests.pg import docker_available

HERE = Path(__file__).resolve().parent / "renderer"
ROOT = Path(__file__).resolve().parents[3]

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not (ROOT / "compose.yml").is_file(), reason="no repository root here"),
]


class Stack:
    def __init__(self, project: str, image: str) -> None:
        self.compose = [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(HERE / "compose.renderer.yml"),
        ]
        self.env = {
            **os.environ,
            "RENDERER_TEST_IMAGE": image,
            "POSTGRES_USER": "unused",
            "POSTGRES_PASSWORD": "unused",
            "POSTGRES_DB": "unused",
        }
        self.ids: dict[str, str] = {}

    def run(self, *arguments: str, check: bool = True) -> str:
        done = subprocess.run(
            [*self.compose, *arguments],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        if check and done.returncode != 0:
            raise AssertionError(
                f"compose {arguments[:3]} failed:\n{done.stdout}\n{done.stderr[-3000:]}"
            )
        return done.stdout

    def container(self, service: str) -> str:
        if service not in self.ids:
            self.ids[service] = self.run("ps", "-q", service).strip()
        return self.ids[service]

    def execute(self, service: str, *command: str, stdin: bytes = b"") -> bytes:
        done = subprocess.run(
            ["docker", "exec", "-i", self.container(service), *command],
            input=stdin,
            capture_output=True,
            check=False,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr[-2000:]
        return done.stdout

    def relay(self, service: str, *arguments: str, stdin: bytes = b"") -> Any:
        return json.loads(
            self.execute(service, "python", "-I", "/driver/relay.py", *arguments, stdin=stdin)
        )

    def connect(self, service: str, host: str, port: int) -> bool:
        """A direct TCP connection from inside `service` (the renderer has no volume, so the
        code goes on the command line)."""
        code = (
            "import socket, sys\ntry:\n"
            " socket.create_connection((sys.argv[1], int(sys.argv[2])), 5).close()"
            "\nexcept OSError:\n print('refused')\nelse:\n print('ok')"
        )
        return self.execute(service, "python", "-I", "-c", code, host, str(port)) == b"ok\n"

    def inspect(self, service: str) -> dict:
        out = subprocess.run(
            ["docker", "inspect", self.container(service)],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(out.stdout)[0]

    def render(self, html: str, host: str = "renderer", fonts: bool = True) -> Rendered:
        """One render from where the worker stands, checked by the worker's own code (without
        the font check when `fonts` is False)."""
        answer = self.relay("client", "render", host, stdin=html.encode())
        body = base64.b64decode(answer["body"])
        if answer["status"] != 200:
            raise RenderError(render_client.REFUSALS.get(answer["status"], "render_failed"))
        if not fonts:
            return Rendered(pdf=body, pages=int(answer["pages"]))
        pages = render_client._check(body, int(answer["pages"]), html)
        return Rendered(pdf=body, pages=pages)


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    if not docker_available():
        pytest.skip("Docker is not available")
    image = os.environ.get("RENDERER_TEST_IMAGE", "")
    built = not image
    if built:
        image = f"jobapp-renderer:test-{os.getpid()}"
        done = subprocess.run(
            ["docker", "build", "-q", "-t", image, str(ROOT / "renderer")],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"the renderer image did not build:\n{done.stderr[-3000:]}"
    running = Stack(f"jobapp-renderer-test-{os.getpid()}", image)
    try:
        running.run("up", "-d", "--wait", "--wait-timeout", "180")
        yield running
    finally:
        running.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)
        if built:
            subprocess.run(["docker", "rmi", image], capture_output=True, check=False)


def text_of(pdf: bytes) -> str:
    document = pdfium.PdfDocument(pdf)
    try:
        return "".join(page.get_textpage().get_text_range() for page in document)
    finally:
        document.close()


def test_the_container_runs_as_compose_writes_it(stack: Stack) -> None:
    found = stack.inspect("renderer")
    config, host = found["Config"], found["HostConfig"]
    assert config["User"] == "10003:10003"
    assert (
        stack.execute("renderer", "python", "-I", "-c", "import os; print(os.getuid())")
        == b"10003\n"
    )
    assert host["ReadonlyRootfs"] is True
    assert list(host["Tmpfs"]) == ["/tmp"]  # noqa: S108  (a path inside the container)
    assert host["CapDrop"] == ["ALL"]
    assert not host["CapAdd"]
    assert host["SecurityOpt"] == ["no-new-privileges:true"], "Docker's default seccomp profile"
    assert host["PidsLimit"] == 128
    assert host["Memory"] == 256 * 1024 * 1024
    assert host["IpcMode"] == "none"
    assert host["Privileged"] is False
    assert not host["PortBindings"]
    names = {entry.split("=", 1)[0] for entry in config["Env"]}
    image_only = {"PATH", "LANG", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256", "HOME"}
    image_only |= {"PLAYWRIGHT_BROWSERS_PATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED"}
    assert names <= image_only, names
    assert list(found["NetworkSettings"]["Networks"]) == [f"{stack.compose[3]}_rendering"]


@pytest.mark.parametrize("name", render_html.FIXTURES)
def test_every_fixture_renders_with_the_fonts_it_asks_for(stack: Stack, name: str) -> None:
    rendered = stack.render(render_html.build(name, 7))
    assert rendered.pages == 1
    assert "Alex Example" in text_of(rendered.pdf)


def test_the_fit_loop_on_the_real_engine(stack: Stack) -> None:
    async def render(html: str) -> Rendered:
        return await asyncio.to_thread(stack.render, html)

    def fitted(name: str) -> fit.Fitted:
        return asyncio.run(
            fit.fit(render_html.STEPS, lambda step: render_html.build(name, step), render=render)
        )

    short, tightest = fitted("cv-short"), fitted("cv-tightest")
    assert (short.step, short.fit, short.renders) == (0, True, 1)
    assert (tightest.step, tightest.fit, tightest.renders) == (7, True, 4)
    assert stack.render(render_html.build("cv-tightest", 6)).pages == 2


def test_an_html_of_1_mb_renders(stack: Stack) -> None:
    html = render_html.build("cv-short", 0).replace(
        "</body>", "<!--" + "x" * (1024 * 1024) + "--></body>"
    )
    assert len(html.encode()) > 1024 * 1024
    assert stack.render(html).pages == 1


def test_a_missing_font_fails_loudly(stack: Stack) -> None:
    html = render_html.build("cv-short", 0).replace('"DejaVu Sans"', '"Tpl Missing"')
    with pytest.raises(RenderError) as raised:
        stack.render(html)
    assert raised.value.code == "render_failed"


def test_with_the_network_open_the_interception_alone_refuses_every_outside_address(
    stack: Stack,
) -> None:
    marker = secrets.token_hex(12)
    stack.execute("renderer-open", "sh", "-c", f"echo {marker} > /tmp/marker.txt")
    own = stack.inspect("renderer-open")["NetworkSettings"]["Networks"]
    address = next(v["IPAddress"] for k, v in own.items() if k.endswith("_open"))
    html = """<!doctype html><html><head><meta charset=utf-8>
<link rel=stylesheet href="http://listener:8080/style.css">
<style>@import url("https://listener:8443/import.css");
@font-face{font-family:"Far";src:url("http://listener:8080/font.woff2")}
body{font-family:"DejaVu Sans";background:url("http://listener:8443/bg.png")}</style></head>
<body><p>Alex Example</p><img src="http://listener:8080/pixel.png"><img src="https://listener:8443/pixel.png">
<iframe src="file:///tmp/marker.txt"></iframe><img src="file:///tmp/marker.txt">
<object data="http://listener:8080/object"></object><video src="http://listener:8080/video.mp4"></video>
<script>new WebSocket("ws://listener:8080/socket"); fetch("http://listener:8080/fetch")</script>
</body></html>"""
    # The frame and the object draw their placeholders in Chromium's default font, which the
    # font check rightly refuses: this test is about requests, so it reads the PDF without it.
    rendered = stack.render(html, host="renderer-open", fonts=False)
    assert "Alex Example" in text_of(rendered.pdf)
    assert marker not in text_of(rendered.pdf), "file:// was read"
    seen = [line for line in stack.relay("listener", "seen") if line.endswith(" " + address)]
    assert seen == [], f"the renderer reached the listener: {seen}"
    # The network really is open: a direct connection from the same container gets through.
    assert stack.connect("renderer-open", "listener", 8080)
    assert any(line.endswith(" " + address) for line in stack.relay("listener", "seen"))


def test_with_the_interception_out_of_the_way_the_internal_network_alone_refuses(
    stack: Stack,
) -> None:
    # The relay inside the renderer's own container opens sockets itself: no Chromium, no route.
    for host, port in (("listener", 8080), ("1.1.1.1", 443), ("8.8.8.8", 53), ("example.org", 80)):
        assert not stack.connect("renderer", host, port), host
    assert stack.relay("client", "connect", "renderer", "7071") == {"ok": True}
