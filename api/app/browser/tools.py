"""The browser's tools for the code of the api and the worker (browser B5): what the apply
adapters (apply A4), the apply run (A5) and the gate (A6) call to drive the lease they hold.

`LeaseTools` is bound to one person and one lease: it is made from what `Leases.acquire` gave
the caller that started the lease (its id and token), and before every call it asks the store
whether that person still holds that lease (`Leases.holds`, one Redis read): a lease of another
person, an ended one or a made up id is refused here (`BrowserError("ended")`) before anything
reaches browserd, which then checks the token itself. Each method is one tool of browserd's
tool layer (browser/browserd/tools/actions.py) with Playwright MCP's argument names, and answers
its result: `ok` true, or false with the refusal's code (`submit`, `danger`, `host_not_allowed`,
`stale_ref`, ...). A refusal is a result, not an error.

`gate_click` is the one call that may click a submit element: it needs the gate's capability,
which `Leases.acquire` returns on the starting call of an `apply_submit` or `profile_edit` lease
only (`Acquired.capability`). It is a function of its own and not a method of `LeaseTools`, so a
binding of these tools for an agent never carries it.
"""

from typing import Any

from app.browser.client import BrowserdClient, BrowserError
from app.browser.leases import Leases


class LeaseTools:
    def __init__(
        self, leases: Leases, client: BrowserdClient, user_id: str, lease_id: str, token: str
    ) -> None:
        self.leases, self.client = leases, client
        self.user_id, self.lease_id, self._token = user_id, lease_id, token

    def __repr__(self) -> str:  # never the token
        return f"LeaseTools(lease_id={self.lease_id!r})"

    async def _held(self) -> None:
        if not await self.leases.holds(self.user_id, self.lease_id):
            raise BrowserError("ended")

    async def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._held()
        return await self.client.tool(self.lease_id, self._token, name, args or {})

    async def put_file(self, name: str, data: bytes) -> dict[str, Any]:
        """Places a PDF on the lease under `name`, for `upload`."""
        await self._held()
        return await self.client.put_file(self.lease_id, self._token, name, data)

    async def snapshot(self) -> dict[str, Any]:
        return await self.call("browser_snapshot")

    async def navigate(self, url: str) -> dict[str, Any]:
        return await self.call("browser_navigate", {"url": url})

    async def click(self, ref: str, element: str = "") -> dict[str, Any]:
        return await self.call("browser_click", {"ref": ref, "element": element})

    async def type(
        self, ref: str, text: str, element: str = "", submit: bool = False
    ) -> dict[str, Any]:
        args = {"ref": ref, "element": element, "text": text, "submit": submit}
        return await self.call("browser_type", args)

    async def select_option(self, ref: str, values: list[str], element: str = "") -> dict[str, Any]:
        return await self.call(
            "browser_select_option", {"ref": ref, "element": element, "values": values}
        )

    async def check(self, ref: str, checked: bool = True, element: str = "") -> dict[str, Any]:
        args = {"ref": ref, "element": element, "checked": checked}
        return await self.call("browser_check", args)

    async def upload(self, ref: str, names: list[str], element: str = "") -> dict[str, Any]:
        return await self.call(
            "browser_file_upload", {"ref": ref, "element": element, "paths": names}
        )

    async def press_key(self, key: str) -> dict[str, Any]:
        return await self.call("browser_press_key", {"key": key})

    async def wait_for(
        self, *, time: float | None = None, text: str | None = None, text_gone: str | None = None
    ) -> dict[str, Any]:
        args: dict[str, Any] = {}
        if time is not None:
            args["time"] = time
        if text is not None:
            args["text"] = text
        if text_gone is not None:
            args["textGone"] = text_gone
        return await self.call("browser_wait_for", args)

    async def screenshot(self, *, full_page: bool = False, kind: str = "png") -> dict[str, Any]:
        return await self.call("browser_take_screenshot", {"fullPage": full_page, "type": kind})

    async def tabs(self, action: str = "list", index: int | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"action": action}
        if index is not None:
            args["index"] = index
        return await self.call("browser_tabs", args)


async def gate_click(
    tools: LeaseTools, ref: str, capability: str, element: str = ""
) -> dict[str, Any]:
    """The apply gate's click on the submit element (apply A6's submit.py, nothing else)."""
    await tools._held()
    return await tools.client.gate_click(tools.lease_id, tools._token, ref, capability, element)
