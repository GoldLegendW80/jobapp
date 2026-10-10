"""A stand-in for the one Anthropic API call the credential check makes, for e2e runs only.

POST /api/me/model-credential/verify lists one model (GET /v1/models?limit=1) with the user's
key in X-Api-Key (api/app/agents/verify.py). With MODEL_API_BASE_URL=http://anthropicstub:8080
(tests/anthropic-stub.compose.yml, refused when APP_ENV is production) the api asks this server
instead, so the journey never sends a key to Anthropic. The answer depends on the key:
- containing "e2egood": 200 with an empty model list (verified);
- containing "e2ebusy": 429 with Retry-After: 5 (try again later);
- anything else: 401 (invalid credential).
/healthz answers 200 for the Compose healthcheck. Nothing is logged about the keys.

Agents A2: POST /v1/messages, the Messages API as the Claude Code CLI calls it when the worker
points it here (ANTHROPIC_BASE_URL from MODEL_API_BASE_URL, refused in production). It lets the
real SDK and CLI run end to end with no model: the CLI's memory under two concurrent runs, the
in-process MCP tool through the real CLI. A key without "e2egood" gets 401. Otherwise:
- when the last user turn holds a tool_result: a short final answer;
- when the request offers mcp__app__get_job and the prompt names a "Job id: <uuid>": a tool_use
  of get_job with that id;
- when the prompt says "try bash": a tool_use of Bash (a tool the agent does not have);
- else a text answer streamed in STUB_CHUNKS pieces STUB_DELAY_S apart (environment variables,
  default 20 pieces 0.5 s apart: a run of about ten seconds).
Streaming follows the documented event sequence (message_start, content_block_start, deltas,
content_block_stop, message_delta with usage, message_stop); without "stream" one JSON message.

    python3 tests/anthropic_stub.py [port]        default port 8080, standard library only
"""

import json
import os
import re
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

CHUNKS = int(os.environ.get("STUB_CHUNKS", "20"))
DELAY_S = float(os.environ.get("STUB_DELAY_S", "0.5"))
JOB_ID = re.compile(r"Job id: ([0-9a-f-]{36})")


def _texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    out = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            out.append(str(block.get("text", "")))
    return out


def plan(body: dict[str, Any]) -> tuple[str, Any]:
    """("text", the answer) or ("tool_use", (name, input))."""
    messages = body.get("messages") or []
    answered = any(
        isinstance(b, dict) and b.get("type") == "tool_result"
        for m in messages
        if isinstance(m.get("content"), list)
        for b in m["content"]
    )
    if answered:
        return "text", "Done: the tool answered."
    prompt = " ".join(
        t for m in messages if m.get("role") == "user" for t in _texts(m.get("content"))
    )
    tools = {t.get("name") for t in body.get("tools") or [] if isinstance(t, dict)}
    found = JOB_ID.search(prompt)
    if found and "mcp__app__get_job" in tools:
        return "tool_use", ("mcp__app__get_job", {"job_id": found.group(1)})
    if "try bash" in prompt.lower():
        return "tool_use", ("Bash", {"command": "env"})
    return "text", "Stub answer: " + " ".join(prompt.split()[:12])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return  # never print request lines (they are harmless, but keep the logs quiet)

    def _send(
        self, status: int, body: object, headers: dict[str, str] | None = None
    ) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send(200, {"ok": True})
            return
        if path != "/v1/models":
            self._send(404, {"type": "error", "error": {"type": "not_found_error"}})
            return
        key = self.headers.get("x-api-key", "")
        if "e2egood" in key:
            self._send(
                200, {"data": [], "has_more": False, "first_id": None, "last_id": None}
            )
        elif "e2ebusy" in key:
            self._send(
                429,
                {
                    "type": "error",
                    "error": {"type": "rate_limit_error", "message": "slow down"},
                },
                {"Retry-After": "5"},
            )
        else:
            self._send(
                401,
                {
                    "type": "error",
                    "error": {"type": "authentication_error", "message": "invalid"},
                },
            )

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if path != "/v1/messages":
            self._send(404, {"type": "error", "error": {"type": "not_found_error"}})
            return
        key = self.headers.get("x-api-key", "") or self.headers.get("authorization", "")
        if "e2egood" not in key:
            self._send(
                401,
                {
                    "type": "error",
                    "error": {"type": "authentication_error", "message": "invalid"},
                },
            )
            return
        body = json.loads(raw or b"{}")
        kind, value = plan(body)
        model = body.get("model", "claude-opus-5-5")
        message_id = "msg_stub_" + uuid.uuid4().hex[:16]
        if kind == "tool_use":
            name, _args = value
            block = {
                "type": "tool_use",
                "id": "toolu_stub_" + uuid.uuid4().hex[:12],
                "name": name,
            }
            stop = "tool_use"
        else:
            block = {"type": "text", "text": ""}
            stop = "end_turn"
        usage = {"input_tokens": 120, "output_tokens": 30}
        if not body.get("stream"):
            full = dict(block)
            if kind == "tool_use":
                full["input"] = value[1]
            else:
                full["text"] = value
            self._send(
                200,
                {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [full],
                    "stop_reason": stop,
                    "stop_sequence": None,
                    "usage": usage,
                },
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def event(name: str, data: dict[str, Any]) -> None:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": usage["input_tokens"],
                        "output_tokens": 1,
                    },
                },
            },
        )
        start = dict(block, input={}) if kind == "tool_use" else block
        event(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": start},
        )
        if kind == "tool_use":
            delta = {"type": "input_json_delta", "partial_json": json.dumps(value[1])}
            event(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": delta},
            )
        else:
            words = str(value).split(" ")
            per = max(1, len(words) // max(1, CHUNKS))
            pieces = [" ".join(words[i : i + per]) for i in range(0, len(words), per)]
            pieces += ["."] * max(0, CHUNKS - len(pieces))
            for index, piece in enumerate(pieces):
                text = piece if index == 0 else " " + piece
                delta = {"type": "text_delta", "text": text}
                event(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 0, "delta": delta},
                )
                time.sleep(DELAY_S)
        event("content_block_stop", {"type": "content_block_stop", "index": 0})
        event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": usage["output_tokens"]},
            },
        )
        event("message_stop", {"type": "message_stop"})


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
