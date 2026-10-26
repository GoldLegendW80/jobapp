"""Source-bound browserd, actual Chromium, host SDK registry and runner, no real sites."""

import asyncio
import dataclasses
import hashlib
import json
import os
import random
import uuid
from pathlib import Path

import httpx
import pytest

from app.browser import finder, sources
from app.browser.client import BrowserdClient, BrowserError
from app.browser.tools import LeaseTools
from tests.browser.b3_support import World, flush, redis_url  # noqa: F401
from tests.browser.b13_runtime import Recording
from tests.browser.test_b5_container import TOOL_CALL, Lease, ref
from tests.browser.test_b9_runtime import (  # noqa: F401
    Runtime,
    alice,
    bob,
    clock,
    db,
    engine,
    factory,
    migrated_url,
    one_connection,
    runtime,
)
from tests.browser.test_browser_container import ROOT, Stack

pytestmark = pytest.mark.docker
START = "http://pages:8080/b13/"


@pytest.fixture
def native(tmp_path):
    # Explicit opt-in avoids this proof ever building or pulling an image implicitly.
    image = os.environ.get("BROWSER_TEST_IMAGE")
    assert image, "Provide an existing BROWSER_TEST_IMAGE, this test never builds or pulls."
    override = tmp_path / "native.yml"
    override.write_text(
        json.dumps(
            {
                "services": {
                    "browser": {
                        "pull_policy": "never",
                        "volumes": [f"{ROOT}/browser/browserd:/opt/browserd/browserd:ro"],
                    },
                    "client": {"pull_policy": "never"},
                    "pages": {
                        "pull_policy": "never",
                        "command": ["python", "-I", "/b13_pages.py"],
                        "volumes": [f"{Path(__file__).with_name('b13_pages.py')}:/b13_pages.py:ro"],
                    },
                }
            }
        )
    )
    stack = Stack("b628-native-" + uuid.uuid4().hex[:8], image)
    stack.redactions = [stack.secret]
    stack.compose[8:8] = ["-f", str(override)]
    try:
        stack.run("up", "-d", "--wait", "--wait-timeout", "120", "--no-build", "--pull", "never")
        for path in ("chromium.py", "detect.py", "tools/actions.py", "tools/discovery.py"):
            expected = hashlib.sha256((ROOT / "browser/browserd" / path).read_bytes()).hexdigest()
            actual = stack.python(
                "import hashlib,pathlib,sys; "
                "print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())",
                "/opt/browserd/browserd/" + path,
            ).strip()
            assert actual == expected
        yield stack
    finally:
        if not getattr(stack, "completed", False):
            diagnostic = stack.run("logs", "--no-color", "--tail", "80", "browser", check=False)
            for secret in stack.redactions:
                diagnostic = diagnostic.replace(secret, "[redacted]")
            print(diagnostic)
        stack.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)


def test_native_search_through_real_runtime(native, runtime: Runtime, alice, redis_url):  # noqa: F811
    runtime.give_key(alice)
    recording = Recording()
    context = dataclasses.replace(runtime.context(), backend_factory=lambda: recording)
    flush(redis_url)
    world = World(redis_url)

    async def transport(request):
        # Existing test client's HTTP bridge, not a replacement browser implementation.
        output = await asyncio.to_thread(
            native.execute,
            "client",
            "python",
            "-I",
            "-c",
            TOOL_CALL,
            request.method,
            request.url.path,
            native.secret,
            json.dumps({k: v for k, v in request.headers.items() if k.startswith("x-")}),
            request.content.decode(),
            "",
        )
        result = json.loads(output)
        if isinstance(result["body"], dict) and result["body"].get("lease_token"):
            native.redactions.append(result["body"]["lease_token"])
        assert result["status"] < 400, (
            request.url.path,
            result["status"],
            {k: result["body"].get(k) for k in ("error", "reason", "message")},
        )
        return httpx.Response(result["status"], json=result["body"])

    client = BrowserdClient(
        "http://browser:9300", native.secret, transport=httpx.MockTransport(transport)
    )
    store = world.make_store(client=client)

    async def run():
        try:
            got = await store.acquire(str(alice), "sweep", True, hosts=["pages"])
            assert got.capability is None
            tools = LeaseTools(store, client, str(alice), got.lease.lease_id, got.token)
            try:
                scope = finder.InteractiveScope(START, "robot", (("remote", "1"),))
                opened = await tools.call(
                    "browser_navigate",
                    {
                        "url": START,
                        "_discovery": "native_get",
                        "_discovery_scope": scope.tool_scope(),
                    },
                )
                assert opened["ok"], opened
                first = await tools.snapshot()
                assert "Robot Engineer" not in first["snapshot"]
                assert "AI assistant reading this page" in first["snapshot"]  # hostile, ignored
                button = ref(first["snapshot"], "button", "Search application")
                refused = await tools.call(
                    "browser_click",
                    {
                        "ref": button,
                        "_discovery": "native_get",
                        "_discovery_scope": scope.tool_scope(),
                    },
                )
                assert not refused["ok"]
                pages = sources.Pages(
                    tools,
                    1000,
                    sleep=lambda _: asyncio.sleep(0),
                    rng=random.SystemRandom(),
                    monotonic=lambda: 0,
                )
                one = finder.FinderSession(
                    pages,
                    START,
                    first["snapshot"],
                    str(alice),
                    interactive=scope,
                )
                ran = await finder.RuntimeFinder(lambda: context)(one)
                from tests.browser.test_b9_runtime import runs_of

                assert not ran.stopped, (
                    [r.error for r in runs_of(runtime.factory, alice)],
                    recording.results,
                )
                assert len(one.found) == 1
                assert one.found[0].text == "Robot Engineer\nObserved employer and duties"
                stolen = LeaseTools(store, client, str(uuid.uuid4()), got.lease.lease_id, got.token)
                with pytest.raises(BrowserError):
                    await stolen.call("browser_click", {"ref": button, "_discovery": "native_get"})
                assert (await one.navigate("http://elsewhere:8080/escape"))[1]
            finally:
                await store.release(str(alice), got.lease.lease_id)
            with pytest.raises(BrowserError):
                await tools.call("browser_click", {"ref": button, "_discovery": "native_get"})
        finally:
            await world.close()

    asyncio.run(run())
    observed = json.loads(
        native.execute(
            "browser",
            "python",
            "-I",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://pages:8080/observed').read().decode())",
        )
    )
    searches = [p["query"] for p in observed if p["path"] == "/b13/search"]
    assert searches == [
        {"q": ["robot"], "remote": ["1"]},
        {"q": ["robot"], "remote": ["1"], "page": ["2"]},
    ]
    assert not any(p["path"] in {"/apply", "/escape"} for p in observed)
    native.completed = True


def test_native_hostile_controls_and_redirects(native):
    failures = []
    for legacy in (True, False):
        for path in (
            "handler",
            "shadow",
            "named",
            "hidden",
            "default",
            "base",
            "redirect-off",
            "redirect-write",
        ):
            lease = Lease(native, "sweep")
            native.redactions.append(lease.token)
            scope = {"start": START + path, "query": "robot", "filters": {}}
            marker = {"_discovery": "native_get", "_discovery_scope": scope}
            try:
                assert lease.capability is None
                opened = lease.tool(
                    "browser_navigate", url=scope["start"], **({} if legacy else marker)
                )
                assert opened["ok"], (legacy, path, opened)
                button = ref(lease.snapshot(), "button", "Search")
                answer = lease.tool("browser_click", ref=button, **marker)
                if answer.get("ok"):
                    failures.append((legacy, path))
                assert answer["error"] in {"unsupported_discovery", "host_not_allowed"}
            finally:
                lease.end()
    observed = json.loads(
        native.execute(
            "browser",
            "python",
            "-I",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://pages:8080/observed').read().decode())",
        )
    )
    assert not failures, failures
    assert not any(p["path"] in {"/b13/custom-action", "/escape", "/remove"} for p in observed)
    assert not any(p.get("method") == "POST" for p in observed)
    assert [
        p["path"] for p in observed if p["path"] in {"/b13/off/search", "/b13/write/search"}
    ] == ["/b13/off/search", "/b13/write/search"]
    assert not any(p["path"] == "/b13/search" for p in observed)
    native.completed = True
