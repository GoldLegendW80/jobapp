"""Browser B23 (issue 780): the website search died on its first screenshot, "The agent failed
(CLIJSONDecodeError)". Claude Code writes each message to the SDK as one line, and the line that
echoes a tool's answer holds the screenshot as base64; the SDK refuses a line longer than its
max_buffer_size, 1 MiB by default, and no run set it. Here the SDK's own transport reads a stand
in Claude Code that writes that line: with the SDK's default it fails as the run did, with the
options every run gets (sdk_options.fixed) it reads it; and a screenshot as the model now gets it
(app/browser/screenshot.py) is far under both.
"""

import asyncio
import base64
import gc
import io
import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, CLIJSONDecodeError
from claude_agent_sdk._internal.transport.subprocess_cli import (
    _DEFAULT_MAX_BUFFER_SIZE,
    SubprocessCLITransport,
)
from PIL import Image

from app.agents import sdk_options
from app.browser import screenshot

# The bound the finder gave a screenshot before B23 (finder.SHOT_MAX), in base64 characters.
OLD_SHOT_MAX = 1_500_000


def tool_answer_line(data: str, mime: str) -> str:
    """The line Claude Code writes for a tool's answer that is one image (stream-json)."""
    message = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "tool_use_id": "toolu_01B23ExampleShot",
                    "type": "tool_result",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "data": data, "media_type": mime},
                        }
                    ],
                }
            ],
        },
        "parent_tool_use_id": None,
        "session_id": "00000000-0000-4000-8000-0000000000b2",
    }
    return json.dumps(message) + "\n"


def stand_in(tmp_path: Path, line: str) -> Path:
    """An executable that writes `line` on its standard output and ends, whatever its arguments."""
    (tmp_path / "line.json").write_text(line)
    cli = tmp_path / "claude"
    cli.write_text(
        f"#!{sys.executable} -I\n"
        "import sys\n"
        f"sys.stdout.write(open({str(tmp_path / 'line.json')!r}).read())\n"
        "sys.stdout.flush()\n"
    )
    cli.chmod(0o700)
    return cli


def read(cli: Path, tmp_path: Path, **options: Any) -> list[dict[str, Any]]:
    async def go() -> list[dict[str, Any]]:
        transport = SubprocessCLITransport(
            prompt="hello", options=ClaudeAgentOptions(cli_path=cli, cwd=tmp_path, **options)
        )
        await transport.connect()
        try:
            return [message async for message in transport.read_messages()]
        finally:
            await transport.close()

    return asyncio.run(go())


def refused(cli: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **options: Any) -> None:
    """The read fails on the line's length. The SDK's close leaves open the output pipe of the
    process it stopped (claude_agent_sdk 0.2.163): its warning is collected here, not in a later
    test."""
    left: list[Any] = []
    monkeypatch.setattr(sys, "unraisablehook", left.append)
    with pytest.raises(CLIJSONDecodeError):
        read(cli, tmp_path, **options)
    gc.collect()
    assert all(isinstance(u.exc_value, ResourceWarning) for u in left), left


def png_of_noise(width: int, height: int) -> str:
    """The hardest page to compress: every pixel random (a fixed seed)."""
    pixels = random.Random(780).randbytes(width * height * 3)  # noqa: S311  a fixed page
    out = io.BytesIO()
    Image.frombytes("RGB", (width, height), pixels).save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode("ascii")


@pytest.fixture(autouse=True)
def _no_version_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")


def test_a_screenshot_the_old_bound_let_through_fails_the_sdk_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    line = tool_answer_line("A" * OLD_SHOT_MAX, "image/png")
    assert _DEFAULT_MAX_BUFFER_SIZE < len(line) < sdk_options.MAX_BUFFER_SIZE
    cli = stand_in(tmp_path, line)
    refused(cli, tmp_path, monkeypatch)
    [message] = read(cli, tmp_path, **sdk_options.fixed())
    assert message["message"]["content"][0]["content"][0]["source"]["data"] == "A" * OLD_SHOT_MAX


def test_the_bound_is_kept_a_line_past_it_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    line = tool_answer_line("A" * sdk_options.MAX_BUFFER_SIZE, "image/png")
    refused(stand_in(tmp_path, line), tmp_path, monkeypatch, **sdk_options.fixed())


def test_a_screenshot_as_the_model_gets_it_is_far_under_both_bounds(tmp_path: Path) -> None:
    shot = screenshot.for_model(png_of_noise(1440, 900))
    assert shot is not None
    line = tool_answer_line(shot, screenshot.MIME)
    assert len(line) < _DEFAULT_MAX_BUFFER_SIZE
    assert len(line) * 8 < sdk_options.MAX_BUFFER_SIZE
    [message] = read(stand_in(tmp_path, line), tmp_path)
    assert message["message"]["content"][0]["content"][0]["source"]["data"] == shot
