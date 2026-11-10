"""Browser B23 (issue 780): the apply agent's browser_take_screenshot answers the same bounded
JPEG as the search agent's (app/browser/screenshot.py), so a busy form page no longer reaches the
SDK as a PNG over its line bound; what is no image answers a text."""

from __future__ import annotations

import asyncio
import base64
import io
import random
from typing import Any

from PIL import Image

from app.apply import applier
from app.browser import screenshot


def png_of_noise(width: int, height: int) -> str:
    pixels = random.Random(780).randbytes(width * height * 3)  # noqa: S311  a fixed page
    out = io.BytesIO()
    Image.frombytes("RGB", (width, height), pixels).save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode("ascii")


class Shot:
    def __init__(self, data: str) -> None:
        self.data, self.calls = data, []

    async def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((name, args))
        return {"ok": True, "data": self.data}


def take(data: str) -> tuple[Any, bool, Shot]:
    async def no(*_: Any) -> Any:
        return None

    tools = Shot(data)
    made = applier.ApplySession(tools=tools, capability="cap", stop=no, arm=no)
    answer, is_error = asyncio.run(made.screenshot())
    return answer, is_error, tools


def test_the_apply_agent_sees_a_bounded_jpeg_of_a_page_over_the_sdk_default() -> None:
    data = png_of_noise(1440, 900)
    assert len(data) > 1024 * 1024
    answer, is_error, tools = take(data)
    assert not is_error
    assert (answer["type"], answer["mimeType"]) == ("image", screenshot.MIME)
    raw = base64.b64decode(answer["data"], validate=True)
    assert len(raw) <= screenshot.SHOT_BYTES
    image = Image.open(io.BytesIO(raw))
    assert (image.format, image.size) == ("JPEG", (1440, 900))
    assert tools.calls == [("browser_take_screenshot", {"type": "png"})]


def test_what_is_no_image_answers_a_text() -> None:
    answer, is_error, _ = take("aGk=")
    assert (answer, is_error) == (screenshot.UNREAD, True)
