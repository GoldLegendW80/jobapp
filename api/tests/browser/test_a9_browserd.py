"""browserd's side of Apply myself (apply A9), without docker: the `apply_manual` purpose in both
lease tables, the person's input from the start with the app's turn, the watch that is not
activity, the seconds left before the idle timeout, and a file chooser the person opens answered
with a document of the lease. Clocks are injected; ids are made up here."""

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd import config as browserd_config  # noqa: E402
from browserd.leases import Request  # noqa: E402
from browserd.tools import actions  # noqa: E402

from app.browser import leases as api_leases  # noqa: E402
from tests.browser.test_b4_live_unit import VIEWER, World, run  # noqa: E402
from tests.browser.test_b5_actions import Page, tab_of  # noqa: E402
from tests.browser.test_b5_actions import Request as ToolRequest  # noqa: E402


def manual_request() -> Request:
    return Request.parse(
        {"user_id": "a", "profile_id": str(uuid.uuid4()), "uid": 20000,
         "purpose": "apply_manual", "automation": True}
    )  # fmt: skip


def test_apply_manual_is_a_tier_0_session_of_thirty_minutes_with_its_debugging_port() -> None:
    assert browserd_config.LEASE_MAX_S["apply_manual"] == 1800
    assert api_leases.LEASE_MAX_S["apply_manual"] == 1800
    assert api_leases.TIERS["apply_manual"] == 0
    assert manual_request().automation is True
    assert "apply_manual" not in api_leases.PREEMPTS_SWEEP


def _input_route(w: World) -> Any:
    from browserd import app as browserd_app

    service = browserd_app.Service(w.manager, b"e2e-a9")
    service.live = w.live  # type: ignore[attr-defined]
    route = next(r for r in browserd_app.ROUTES if "live/input" in r.pattern.pattern)

    async def call(lease_id: str, body: bytes) -> Any:
        match = route.pattern.fullmatch(f"/leases/{lease_id}/live/input")
        return await route.handler(service, browserd_app.HttpRequest("POST", "/", {}, body), match)

    return call


def test_the_person_holds_the_input_but_during_the_apps_turn() -> None:
    async def go(w: World) -> None:
        made, _ = await w.manager.acquire(manual_request())
        lease_id = made.lease_id
        call = _input_route(w)
        assert w.live.person(lease_id) is True
        assert await call(lease_id, b'{"viewer": null}') == (200, {"input": "agent"})
        assert w.live.person(lease_id) is False
        # A page's own give back (with `from`) never takes the input from the person.
        assert await call(lease_id, f'{{"viewer": "{VIEWER}"}}'.encode()) == (
            200,
            {"input": "person"},
        )
        mine = f'{{"viewer": null, "from": "{VIEWER}"}}'.encode()
        assert await call(lease_id, mine) == (200, {"input": "person"})
        assert w.live.person(lease_id) is True
        assert w.live.holder(lease_id) is None  # no take over: every viewer types
        assert await w.live.agent_turn(lease_id) is None  # the app's tools never wait

    run(go)


def test_the_watch_is_not_activity_and_the_idle_seconds_left_are_answered() -> None:
    async def go(w: World) -> None:
        made, token = await w.manager.acquire(manual_request())
        w.clock.now += 100

        async def read() -> str:
            return "page"

        assert await w.manager.call(made.lease_id, token, read, touch=False) == "page"
        assert made.public(w.clock())["idle_left_s"] == 200
        await w.manager.call(made.lease_id, token, read)
        assert made.public(w.clock())["idle_left_s"] == 300
        w.clock.now += 300
        assert made.expired(w.clock()) == "idle"

    run(go)


class ManualRequest(ToolRequest):
    purpose = "apply_manual"


class Chooser:
    def __init__(self) -> None:
        self.files: list[Any] = []

    async def set_files(self, payloads: Any, **kw: Any) -> None:
        self.files.append(payloads)


def _manual_tab(page: Page) -> actions.Tab:
    tab = tab_of(page)
    tab.request = ManualRequest()
    tab.state.files["cv.pdf"] = b"%PDF-1.4 e2e"
    return tab


def test_a_chooser_the_person_opened_is_answered_with_a_document_of_the_lease() -> None:
    page = Page()
    page.on = lambda event, handler: setattr(page, "chooser_handler", handler)  # type: ignore[attr-defined]
    tab = _manual_tab(page)
    asyncio.run(tab.guard())
    assert callable(page.chooser_handler)  # type: ignore[attr-defined]
    chooser = Chooser()
    page.chooser_handler(chooser)  # type: ignore[attr-defined]
    done = asyncio.run(actions.run(tab, "browser_file_upload", {"paths": ["cv.pdf"]}))
    assert done["ok"] is True
    assert done["uploaded"] == ["cv.pdf"]
    assert chooser.files[0][0]["name"] == "cv.pdf"
    assert tab.state.chooser is None
    again = asyncio.run(actions.run(tab, "browser_file_upload", {"paths": ["cv.pdf"]}))
    assert again["error"] == "no_chooser"
    other = asyncio.run(actions.run(tab, "browser_file_upload", {"paths": ["letter.pdf"]}))
    assert other["error"] == "file_not_given"


def test_only_apply_myself_intercepts_the_file_chooser() -> None:
    page = Page()
    page.on = lambda event, handler: setattr(page, "chooser_handler", handler)  # type: ignore[attr-defined]
    tab = tab_of(page)
    tab.state.files["cv.pdf"] = b"%PDF-1.4 e2e"
    asyncio.run(tab.guard())
    assert not hasattr(page, "chooser_handler")
    with pytest.raises(ValueError, match="ref"):  # bad_args, as before A9 (tools/routes.py)
        asyncio.run(actions.run(tab, "browser_file_upload", {"paths": ["cv.pdf"]}))


@pytest.mark.parametrize("purpose", ["apply_fill", "takeover", "signin"])
def test_other_purposes_keep_their_input_rules(purpose: str) -> None:
    async def go(w: World) -> None:
        request = Request.parse(
            {"user_id": "a", "profile_id": str(uuid.uuid4()), "uid": 20000, "purpose": purpose,
             "automation": True}
        )  # fmt: skip
        made, _ = await w.manager.acquire(request)
        call = _input_route(w)
        if purpose == "apply_fill":
            assert w.live.person(made.lease_id) is False
            assert await call(made.lease_id, f'{{"viewer": "{VIEWER}"}}'.encode()) == (
                200,
                {"input": "person"},
            )
            assert w.live.holder(made.lease_id) == VIEWER
        else:
            assert w.live.person(made.lease_id) is True
        assert made.app_turn is False

    run(go)


def _tool_route(w: World) -> Any:
    from browserd import app as browserd_app

    service = browserd_app.Service(w.manager, b"e2e-a9")
    route = next(r for r in browserd_app.ROUTES if "/tools/" in r.pattern.pattern)

    async def call(lease_id: str, token: str, name: str, body: bytes = b"{}") -> Any:
        match = route.pattern.fullmatch(f"/leases/{lease_id}/tools/{name}")
        req = browserd_app.HttpRequest("POST", "/", {"x-lease-token": token}, body)
        return await route.handler(service, req, match)

    return call


def test_a_take_over_during_the_apps_turn_stops_the_apps_next_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[str] = []

    async def acted(tab: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
        ran.append(name)
        return {"ok": True}

    monkeypatch.setattr(actions, "run", acted)

    async def go(w: World) -> None:
        made, token = await w.manager.acquire(manual_request())
        lease_id = made.lease_id
        tool, live = _tool_route(w), _input_route(w)
        # The person's time: the app reads and answers a chooser, never types nor clicks.
        for name in ("browser_type", "browser_click", "browser_press_key",
                     "browser_select_option", "browser_check", "browser_tabs"):  # fmt: skip
            status, found = await tool(lease_id, token, name)
            assert (status, found["ok"], found["error"]) == (200, False, "person_input")
        for name in ("browser_snapshot", "browser_file_upload", "browser_navigate"):
            assert (await tool(lease_id, token, name))[1] == {"ok": True}
        assert ran == ["browser_snapshot", "browser_file_upload", "browser_navigate"]
        # "Fill my details": the app's turn, its keys reach the page.
        await live(lease_id, b'{"viewer": null}')
        assert (await tool(lease_id, token, "browser_type"))[1] == {"ok": True}
        # The person clicks Take over: the app's next key is refused, the fill stops there.
        assert await live(lease_id, f'{{"viewer": "{VIEWER}"}}'.encode()) == (
            200,
            {"input": "person"},
        )
        status, found = await tool(lease_id, token, "browser_type")
        assert found["error"] == "person_input"
        assert ran.count("browser_type") == 1
        assert w.live.person(lease_id) is True

    run(go)


class _Waiting:
    """Playwright's `expect_file_chooser`: the page's click opens a chooser, which the page's
    listeners receive too (the same object, as Playwright emits one event to both)."""

    def __init__(self, page: Page, chooser: Chooser) -> None:
        self.page, self.chooser = page, chooser
        self.value = asyncio.get_running_loop().create_future()

    async def __aenter__(self) -> "_Waiting":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.page.chooser_handler(self.chooser)  # type: ignore[attr-defined]
        self.value.set_result(self.chooser)


def test_the_chooser_the_apps_own_attach_click_opens_is_never_kept_for_the_person() -> None:
    page = Page()
    page.on = lambda event, handler: setattr(page, "chooser_handler", handler)  # type: ignore[attr-defined]
    tab = _manual_tab(page)
    tab.state.files["letter.pdf"] = b"%PDF-1.4 e2e letter"
    opened = Chooser()
    page.expect_file_chooser = lambda **kw: _Waiting(page, opened)  # type: ignore[attr-defined]
    button = page.add("e9", {"tag": "button", "type": "button", "name": "Attach cover letter"})

    async def element(args: Any) -> tuple[Any, None]:
        return button, None

    async def facts(locator: Any, point: bool = False) -> dict[str, Any]:
        return {"tag": "button", "type": "button"}

    async def approach(locator: Any) -> None:
        return None

    async def go() -> dict[str, Any]:
        await tab.guard()
        tab.element, tab.facts, tab.approach = element, facts, approach  # type: ignore[method-assign]
        tab.judge = lambda facts, action, tool: None  # type: ignore[method-assign]
        return await actions.run(tab, "browser_file_upload", {"ref": "e9", "paths": ["letter.pdf"]})

    done = asyncio.run(go())
    assert done["ok"] is True, done
    assert opened.files[0][0]["name"] == "letter.pdf"
    assert tab.state.chooser is None
    assert "chooser" not in done
    stale = asyncio.run(actions.run(tab, "browser_file_upload", {"paths": ["cv.pdf"]}))
    assert stale["error"] == "no_chooser"
    assert len(opened.files) == 1  # the letter's chooser never receives the CV
