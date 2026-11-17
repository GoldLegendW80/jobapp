"""Browser B25: a private run's events keep a safe preview of an app tool's arguments and the text
of its errors, never a page, a typed text or a script."""

import uuid
from typing import Any

from claude_agent_sdk import ToolResultBlock, ToolUseBlock

from app.agents import runner

NAVIGATE = "mcp__app__browser_navigate"
TYPE = "mcp__app__browser_type"
EVALUATE = "mcp__app__browser_evaluate"
MARKER = "zq7-" + uuid.uuid4().hex  # a planted token: never stored


def recorder(secret: str | None = None) -> runner.Recorder:
    return runner.Recorder(
        context=None,  # type: ignore[arg-type]  tool_call and tool_result never write
        run_id=uuid.uuid4(),
        seq=1,
        secret=secret,
        private=True,
        known_tools=frozenset({NAVIGATE, TYPE, EVALUATE, "Bash"}),
    )


def call(rec: runner.Recorder, name: str, args: dict[str, Any], id_: str = "c1") -> dict[str, Any]:
    return rec.tool_call(ToolUseBlock(id=id_, name=name, input=args))


def test_navigate_keeps_the_address_without_userinfo_or_fragment_and_keeps_the_query() -> None:
    data = call(
        recorder(),
        NAVIGATE,
        {"url": f"https://me:{MARKER}@jobs.example.org/search?q=data&page=2#{MARKER}"},
    )
    assert data["preview"] == {"url": "https://jobs.example.org/search?q=data&page=2"}
    assert data["input_size"] > 0
    assert MARKER not in str(data)


def test_a_secret_query_value_and_the_credential_are_replaced() -> None:
    data = call(
        recorder(secret=MARKER),
        NAVIGATE,
        {"url": f"https://jobs.example.org/a?token=abc&q={MARKER}"},
    )
    address = data["preview"]["url"]
    assert "token=%5Bredacted%5D" in address
    assert "abc" not in address
    assert MARKER not in address


def test_a_relative_address_is_kept_and_a_non_http_one_only_by_length() -> None:
    rec = recorder()
    assert call(rec, NAVIGATE, {"url": "/jobs?q=x#frag"})["preview"] == {"url": "/jobs?q=x"}
    assert call(rec, NAVIGATE, {"url": "javascript:alert(1)"})["preview"] == {"url_length": 19}


def test_typed_text_and_scripts_are_stored_as_lengths_and_marker_args_left_out() -> None:
    rec = recorder()
    typed = call(rec, TYPE, {"ref": "e12", "text": MARKER, "submit": True, "_discovery": "x"})
    assert typed["preview"] == {"ref": "e12", "text_length": len(MARKER), "submit": True}
    script = call(rec, EVALUATE, {"function": f"() => '{MARKER}'"})
    assert script["preview"] == {"function_length": len(f"() => '{MARKER}'")}
    # A ref that is not one short word is a length too.
    odd = call(rec, TYPE, {"ref": f"{MARKER} more", "text": ""})
    assert "ref" not in odd["preview"]
    assert odd["preview"]["ref_length"] > 0
    assert MARKER not in str([typed, script, odd])


def test_claude_code_tools_and_made_up_names_keep_no_preview() -> None:
    rec = recorder()
    assert "preview" not in call(rec, "Bash", {"command": MARKER})
    made_up = call(rec, f"mcp__app__{MARKER}", {"url": "https://jobs.example.org/"})
    assert made_up["name"] == runner.NOT_STORED
    assert "preview" not in made_up


def test_an_app_tool_error_keeps_its_text_bounded_and_a_result_never_its_content() -> None:
    rec = recorder(secret=MARKER)
    call(rec, NAVIGATE, {"url": "https://jobs.example.org/"}, "n1")
    refusal = (
        "Refused: the app does not open this address. Go on another way. "
        f"(rule host_not_allowed) {MARKER} " + "x" * 900
    )
    error = rec.tool_result(ToolResultBlock(tool_use_id="n1", content=refusal, is_error=True))
    assert error["error"].startswith("Refused: the app does not open this address.")
    assert "(rule host_not_allowed)" in error["error"]
    assert MARKER not in error["error"]
    assert len(error["error"]) <= runner.TOOL_ERROR_MAX + len("... [9999 more]")
    page = rec.tool_result(
        ToolResultBlock(tool_use_id="n1", content=f"page {MARKER}", is_error=False)
    )
    assert "error" not in page
    assert "content" not in page
    assert MARKER not in str(page)


def test_an_error_of_a_claude_code_tool_keeps_no_text() -> None:
    rec = recorder()
    call(rec, "Bash", {"command": "ls"}, "b1")
    data = rec.tool_result(ToolResultBlock(tool_use_id="b1", content=MARKER, is_error=True))
    assert "error" not in data
    assert MARKER not in str(data)


def test_an_older_event_shape_is_unchanged_for_a_tool_with_no_arguments() -> None:
    data = call(recorder(), "mcp__app__browser_navigate", {})
    assert data == {"id": "c1", "name": NAVIGATE, "input_size": 2}
