"""The tool layer from the api's side (browser B5): `LeaseTools` and `gate_click` over the real
lease store (Redis), browserd's real control and tool routes and its lease manager with a fake
Chromium (b3_support.FakeBrowserd). What is checked here needs no page: who may call, with which
token and capability, the documents placed on a lease, and the refusals the tool layer answers
before it touches the page. The tools on a real page are test_b5_container.py.
"""

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable, Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.browser.client import BrowserError
from app.browser.tools import LeaseTools, gate_click
from tests.browser.b3_support import World, flush, redis_url

__all__ = ["redis_url"]

A, B = (f"0000000{n}-0000-4000-8000-000000000000" for n in range(1, 3))
PDF = b"%PDF-1.7\n" + secrets.token_hex(64).encode()
HOSTS = ("pages", "*.ashbyhq.com")


@pytest.fixture
def world(redis_url: str) -> Iterator[World]:
    flush(redis_url)
    return World(redis_url)


def run(world: World, scenario: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        try:
            return await scenario()
        finally:
            await world.close()

    return asyncio.run(go())


async def tools_of(world: World, user: str, purpose: str = "apply_fill") -> tuple[LeaseTools, Any]:
    got = await world.store.acquire(user, purpose, True, hosts=HOSTS, submit_selectors=["#go"])
    assert got.lease is not None
    assert got.token
    return LeaseTools(
        world.store, world.browserd.client(), user, got.lease.lease_id, got.token
    ), got


def test_the_lease_carries_its_hosts_and_selectors_to_browserd(world: World) -> None:
    async def go() -> None:
        await tools_of(world, A)
        request = world.browserd.starts[0]
        assert request.hosts == HOSTS
        assert request.submit_selectors == ("#go",)

    run(world, go)


@pytest.mark.parametrize("purpose", ["apply_fill", "full_auto", "sweep", "takeover", "signin"])
def test_no_capability_for_a_purpose_an_agent_or_a_person_drives(
    world: World, purpose: str
) -> None:
    async def go() -> None:
        got = await world.store.acquire(A, purpose, True)
        assert got.lease is not None
        assert got.capability is None
        assert world.browserd.manager.lease is not None
        assert world.browserd.manager.lease.gate_hash == b""

    run(world, go)


@pytest.mark.parametrize("purpose", ["apply_submit", "profile_edit"])
def test_the_gate_purposes_get_a_capability_once(world: World, purpose: str) -> None:
    async def go() -> None:
        got = await world.store.acquire(A, purpose, True)
        assert got.capability
        assert len(got.capability) >= 40
        assert got.capability not in repr(got)
        again = await world.store.acquire(A, purpose, True)
        assert again.lease is not None
        assert again.capability is None
        assert again.token is None
        lease = world.browserd.manager.lease
        assert lease is not None
        assert got.capability.encode() not in lease.gate_hash

    run(world, go)


def test_a_person_naming_anothers_lease_is_refused_before_browserd(world: World) -> None:
    async def go() -> None:
        theirs, _ = await tools_of(world, B)
        before = len(world.browserd.calls)
        # A copies B's lease id and even B's token: the store says A holds no such lease.
        stolen = LeaseTools(world.store, world.browserd.client(), A, theirs.lease_id, theirs._token)
        calls = (
            stolen.snapshot(),
            stolen.navigate("http://pages:8000/"),
            stolen.put_file("cv.pdf", PDF),
            gate_click(stolen, "e1", "x" * 43),
        )
        for call in calls:
            with pytest.raises(BrowserError) as refused:
                await call
            assert refused.value.code == "ended"
        assert world.browserd.calls[before:] == []
        assert repr(theirs) == f"LeaseTools(lease_id={theirs.lease_id!r})"

    run(world, go)


def test_an_ended_lease_answers_ended(world: World) -> None:
    async def go() -> None:
        tools, got = await tools_of(world, A)
        assert await world.store.release(A, got.lease.lease_id)
        with pytest.raises(BrowserError) as ended:
            await tools.snapshot()
        assert ended.value.code == "ended"
        # And straight to browserd, past the store: the lease is not the current one.
        with pytest.raises(BrowserError) as gone:
            await tools.client.tool(tools.lease_id, tools._token, "browser_snapshot")
        assert gone.value.code == "ended"

    run(world, go)


def test_a_wrong_token_is_refused_by_browserd(world: World) -> None:
    async def go() -> None:
        tools, _ = await tools_of(world, A)
        forged = LeaseTools(world.store, tools.client, A, tools.lease_id, secrets.token_urlsafe(32))
        with pytest.raises(BrowserError) as refused:
            await forged.navigate("http://pages:8000/")
        assert refused.value.code == "refused"

    run(world, go)


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("file:///etc/passwd", "refused_scheme"),
        ("chrome://settings", "refused_scheme"),
        ("data:text/html,x", "refused_scheme"),
        ("https://evil.example/", "host_not_allowed"),
        ("https://pages@evil.example/", "bad_url"),
    ],
)
def test_navigation_off_the_list_is_refused_before_the_page(
    world: World, url: str, code: str
) -> None:
    async def go() -> None:
        tools, _ = await tools_of(world, A)
        result = await tools.navigate(url)
        assert result == {"ok": False, "error": code, "message": result["message"]}

    run(world, go)


def test_documents_are_pdfs_of_at_most_20_mb_and_upload_by_name_only(world: World) -> None:
    async def go() -> None:
        tools, _ = await tools_of(world, A)
        assert await tools.put_file("cv.pdf", PDF) == {
            "ok": True,
            "name": "cv.pdf",
            "size": len(PDF),
        }
        for name, data in (("cv.pdf", b"PK\x03\x04zip"), ("../cv.pdf", PDF),
                           ("big.pdf", b"%PDF-" + b"0" * (21 * 1024 * 1024))):  # fmt: skip
            with pytest.raises(BrowserError) as refused:
                await tools.put_file(name, data)
            assert refused.value.code == "refused"
        state = world.browserd.manager.lease.scratch["tools"]
        assert list(state.files) == ["cv.pdf"]
        assert state.files["cv.pdf"] == PDF
        result = await tools.upload("e3", ["letter.pdf"])
        assert result["ok"] is False
        assert result["error"] == "file_not_given"
        result = await tools.upload("e3", ["/etc/passwd"])
        assert result["error"] == "file_not_given"

    run(world, go)


def test_documents_go_with_the_lease(world: World) -> None:
    async def go() -> None:
        tools, got = await tools_of(world, A)
        await tools.put_file("cv.pdf", PDF)
        lease = world.browserd.manager.lease
        state = lease.scratch["tools"]
        await world.store.release(A, got.lease.lease_id)
        assert world.browserd.manager.lease is None
        # Dropped at the end, not when the old context's route handler is collected.
        assert state.files == {}
        second, _ = await tools_of(world, A)
        assert "tools" not in world.browserd.manager.lease.scratch
        assert lease is not world.browserd.manager.lease
        result = await second.upload("e3", ["cv.pdf"])
        assert result["error"] == "file_not_given"

    run(world, go)


def test_the_cap_of_documents_holds_under_concurrent_puts(world: World) -> None:
    # Straight on browserd's route, so both PUTs pass the first check before either is held.
    from browserd.leases import LeaseError
    from browserd.tools import routes

    async def go() -> None:
        tools, got = await tools_of(world, A)
        await tools.put_file("a.pdf", PDF)
        await tools.put_file("b.pdf", PDF)
        svc = SimpleNamespace(manager=world.browserd.manager)

        def put(name: str) -> Awaitable[Any]:
            req = SimpleNamespace(headers={"x-lease-token": got.token}, body=PDF)
            return routes.put_file(svc, req, {"name": name, "lease_id": got.lease.lease_id})

        answers = await asyncio.gather(put("c.pdf"), put("d.pdf"), return_exceptions=True)
        refused = [a for a in answers if isinstance(a, LeaseError)]
        assert len(refused) == 1, answers
        assert (refused[0].status, refused[0].code) == (400, "too_many_files")
        state = world.browserd.manager.lease.scratch["tools"]
        assert len(state.files) == 3
        assert {"a.pdf", "b.pdf"} < set(state.files)

    run(world, go)


def test_the_capability_is_checked_before_the_page(world: World) -> None:
    async def go() -> None:
        tools, got = await tools_of(world, A, "apply_submit")
        # A wrong capability, a capability on another tool, a capability on a lease with none.
        with pytest.raises(BrowserError) as refused:
            await gate_click(tools, "e1", secrets.token_urlsafe(32))
        assert refused.value.code == "refused"
        with pytest.raises(BrowserError):
            await gate_click(tools, "e1", "")
        client = tools.client
        with pytest.raises(BrowserError) as other_tool:
            await client._tool(tools.lease_id, tools._token, "browser_type", {}, got.capability)
        assert other_tool.value.code == "refused"
        await world.store.release(A, got.lease.lease_id)
        fill, _ = await tools_of(world, A, "apply_fill")
        with pytest.raises(BrowserError) as none:
            await gate_click(fill, "e1", got.capability)
        assert none.value.code == "refused"

    run(world, go)


def test_bad_arguments_are_results_not_crashes(world: World) -> None:
    async def go() -> None:
        tools, _ = await tools_of(world, A)
        bad = [
            ("browser_click", {"ref": "#submit"}),
            ("browser_click", {}),
            ("browser_click", {"ref": "e1", "modifiers": ["Control"]}),
            ("browser_click", {"ref": "e1", "button": "back"}),
            ("browser_press_key", {"key": "Control+P"}),
            ("browser_file_upload", {"ref": "e1", "paths": []}),
            ("browser_type", {"ref": "e1", "text": "x" * 10_001}),
            ("browser_select_option", {"ref": "e1", "values": []}),
            ("browser_check", {"ref": "e1", "checked": "yes"}),
        ]
        for name, args in bad:
            result = await tools.call(name, args)
            assert result["ok"] is False, name
            assert result["error"] == "bad_args", (name, result)
        with pytest.raises(BrowserError) as unknown:
            await tools.call("browser_evaluate", {"function": "() => 1"})
        assert unknown.value.code == "ended"  # 404: no such tool on this lease

    run(world, go)


def test_no_token_capability_or_document_byte_in_the_logs(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    async def go() -> tuple[str, str]:
        tools, got = await tools_of(world, A, "apply_submit")
        await tools.put_file("cv.pdf", PDF)
        await tools.navigate("https://evil.example/")
        with pytest.raises(BrowserError):
            await gate_click(tools, "e1", "wrong" * 9)
        return got.token, got.capability

    token, capability = run(world, go)
    logged = caplog.text
    for secret in (token, capability, PDF.decode()[9:], A):
        assert secret not in logged
    assert "PUT /leases/{id}/files/{name}" in logged
    assert "/tools/browser_navigate" in logged


def test_the_transport_never_sees_the_capability_on_an_ordinary_call(world: World) -> None:
    seen: list[httpx.Request] = []

    async def go() -> None:
        tools, _ = await tools_of(world, A, "apply_submit")
        inner = tools.client._transport

        async def spy(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return await inner.handle_async_request(request)

        tools.client._transport = httpx.MockTransport(spy)
        await tools.navigate("https://evil.example/")
        await tools.upload("e1", ["cv.pdf"])

    run(world, go)
    assert seen
    assert all("x-gate-capability" not in r.headers for r in seen)


def test_the_client_checks_a_documents_name_as_browserd_does() -> None:
    from browserd.tools import policy

    from app.browser import client

    assert client.FILE_NAME.pattern == policy.FILE_NAME.pattern
