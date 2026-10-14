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
Ingestion G14, the transcription path through the real CLI (no model: the "transcription" is a
constant sentence per image). Checked before the rules above:
- when the request offers mcp__app__view_image: view_image(0) first; after a result that holds
  an image, store_transcription for the position just viewed; after an answer that names
  next_position, view_image of it, and a final line when it is null; after an answer that lists
  "Positions left: a, b", view_image(a); after anything else a final line;
- when the request offers mcp__app__read_document and no tool has answered yet:
  read_document(0).
GET /_seen answers what the tool results of the requests held, oldest first and at most
SEEN_MAX: per result its blocks, an image as {media_type, bytes, sha256, width, height} (the
size read from the JPEG's own frame header) and a text as {chars, utf8_bytes, sha256, cut},
where `cut` says the CLI replaced it ("[OUTPUT TRUNCATED"). Never the content itself. POST
/_reset empties the list.

Streaming follows the documented event sequence (message_start, content_block_start, deltas,
content_block_stop, message_delta with usage, message_stop); without "stream" one JSON message.

    python3 tests/anthropic_stub.py [port]        default port 8080, standard library only
"""

import base64
import binascii
import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

CHUNKS = int(os.environ.get("STUB_CHUNKS", "20"))
DELAY_S = float(os.environ.get("STUB_DELAY_S", "0.5"))
JOB_ID = re.compile(r"Job id: ([0-9a-f-]{36})")
VIEW_IMAGE = "mcp__app__view_image"
STORE_TRANSCRIPTION = "mcp__app__store_transcription"
READ_DOCUMENT = "mcp__app__read_document"
NEXT_POSITION = re.compile(r'"next_position":\s*(\d+|null)')
POSITIONS_LEFT = re.compile(r"Positions left: (\d+)")
CUT = "[OUTPUT TRUNCATED"
SEEN_MAX = 200
SEEN: list[dict[str, Any]] = []
_seen_lock = threading.Lock()
# Start of frame markers of a JPEG: the segment holds the height, then the width.
_FRAMES = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def transcription_of(position: int) -> str:
    """What the stub "reads" on an image: a constant sentence, never anything of the image."""
    return f"Stub transcription of image {position}."


def jpeg_size(data: bytes) -> tuple[int | None, int | None]:
    """(width, height) from the frame header of a JPEG, (None, None) when there is none."""
    at = 2
    while data[:2] == b"\xff\xd8" and at + 9 <= len(data) and data[at] == 0xFF:
        marker = data[at + 1]
        if marker in _FRAMES:
            return (
                int.from_bytes(data[at + 7 : at + 9], "big"),
                int.from_bytes(data[at + 5 : at + 7], "big"),
            )
        at += 2 + int.from_bytes(data[at + 2 : at + 4], "big")
    return None, None


def _block_seen(block: Any) -> dict[str, Any]:
    if isinstance(block, str):
        block = {"type": "text", "text": block}
    if not isinstance(block, dict):
        return {"type": "other"}
    if block.get("type") == "image":
        source = block.get("source") or {}
        try:
            data = base64.b64decode(str(source.get("data", "")), validate=True)
        except (binascii.Error, ValueError):
            data = b""
        width, height = jpeg_size(data)
        return {
            "type": "image",
            "media_type": source.get("media_type"),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "width": width,
            "height": height,
        }
    if block.get("type") == "text":
        text = str(block.get("text", ""))
        return {
            "type": "text",
            "chars": len(text),
            "utf8_bytes": len(text.encode("utf-8", "surrogatepass")),
            "sha256": hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
            "cut": CUT in text,
        }
    return {"type": str(block.get("type"))}


def _last_results(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool_result blocks that answer the last assistant turn: those of every message after
    it (the CLI may send a result and a note of its own as two user messages)."""
    found: list[dict[str, Any]] = []
    for message in reversed(body.get("messages") or []):
        if not isinstance(message, dict) or message.get("role") == "assistant":
            break
        content = message.get("content")
        if isinstance(content, list):
            found[:0] = [
                b
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
    return found


def _blocks(result: dict[str, Any]) -> list[Any]:
    content = result.get("content")
    return content if isinstance(content, list) else [content or ""]


def remember(body: dict[str, Any]) -> None:
    """Keep what the tool results of this request held: sizes and hashes, never the content."""
    for result in _last_results(body):
        entry = {
            "is_error": bool(result.get("is_error")),
            "blocks": [_block_seen(block) for block in _blocks(result)],
        }
        with _seen_lock:
            SEEN.append(entry)
            del SEEN[:-SEEN_MAX]


def _viewed(messages: list[Any], tool_use_id: Any) -> int:
    """The position the tool call `tool_use_id` asked to view, 0 when it cannot be found."""
    for message in reversed(messages):
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("id") == tool_use_id:
                position = (block.get("input") or {}).get("position")
                return position if isinstance(position, int) else 0
    return 0


def plan_transcription(body: dict[str, Any]) -> tuple[str, Any]:
    """One image at a time: view, store, follow next_position, then one line."""
    results = _last_results(body)
    if not results:
        return "tool_use", (VIEW_IMAGE, {"position": 0})
    last = results[-1]
    blocks = _blocks(last)
    if any(isinstance(b, dict) and b.get("type") == "image" for b in blocks):
        position = _viewed(body.get("messages") or [], last.get("tool_use_id"))
        return "tool_use", (
            STORE_TRANSCRIPTION,
            {"position": position, "text": transcription_of(position)},
        )
    text = " ".join(_texts(blocks))
    following = NEXT_POSITION.search(text) or POSITIONS_LEFT.search(text)
    if following and following.group(1) != "null":
        return "tool_use", (VIEW_IMAGE, {"position": int(following.group(1))})
    return "text", "Transcribed the images of the step."


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
    offered = {t.get("name") for t in body.get("tools") or [] if isinstance(t, dict)}
    if VIEW_IMAGE in offered:
        return plan_transcription(body)
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
    if READ_DOCUMENT in tools:
        return "tool_use", (READ_DOCUMENT, {"offset": 0})
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
        if path == "/_seen":
            with _seen_lock:
                self._send(200, {"results": list(SEEN)})
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
        if path == "/_reset":
            with _seen_lock:
                SEEN.clear()
            self._send(200, {"ok": True})
            return
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
        remember(body)
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
