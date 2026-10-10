"""Spike G0 of the ingestion plan (review of PR #38, finding B2). Throwaway, not product code.

Question: when an in-process app tool of the pinned Claude Agent SDK returns an image block, what
does the real bundled Claude Code CLI send to the Messages API in the next request?

It starts the real CLI through the pinned SDK with the same options as app/agents/runner.py
(`tools=[]`, `dontAsk`, an in-process MCP server, no settings, a scratch HOME) and points it at
tests/anthropic_stub.py running in this process on a loopback port. No model is called and no
credential is used: the key is the literal test string the stub accepts. The stub's `plan` is
replaced so that it asks for the spike's tool, and every request body is summarised (block types,
sizes, hashes; never headers).

    cd <repo root>
    uv run --native-tls --no-project --with claude-agent-sdk==0.2.163 --with pillow==12.3.0 \
        python tasks/evidence/ingestion-g0/spike.py [result.json]
"""

import asyncio
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
STUB_PATH = Path(os.environ.get("SPIKE_STUB", ROOT / "tests" / "anthropic_stub.py"))
KEY = "sk-ant-e2egood-spike-not-a-credential"
TOOL = "mcp__app__view_image"
ENV_KEEP = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}

spec = importlib.util.spec_from_file_location("anthropic_stub", STUB_PATH)
assert spec and spec.loader
stub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stub)
stub.CHUNKS = 1
stub.DELAY_S = 0.0

REQUESTS: list[dict[str, Any]] = []
CASE: dict[str, Any] = {}
ORIGINALS: dict[str, str] = {}  # sha256 of the base64 the tool returned -> label


def _tool_results(body: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for message in body.get("messages") or []:
        content = message.get("content")
        if isinstance(content, list):
            out += [
                b
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
    return out


def _describe(block: Any) -> dict[str, Any]:
    if not isinstance(block, dict):
        return {"type": "str", "chars": len(str(block)), "head": str(block)[:400]}
    kind = block.get("type")
    if kind == "text":
        text = str(block.get("text", ""))
        return {"type": "text", "chars": len(text), "head": text[:400]}
    if kind == "image":
        source = block.get("source") or {}
        data = str(source.get("data", ""))
        digest = hashlib.sha256(data.encode()).hexdigest()
        info: dict[str, Any] = {
            "type": "image",
            "source_type": source.get("type"),
            "media_type": source.get("media_type"),
            "base64_chars": len(data),
            "same_bytes_as_tool_returned": ORIGINALS.get(digest),
        }
        try:
            raw = base64.b64decode(data)
            with Image.open(io.BytesIO(raw)) as image:
                info.update(bytes=len(raw), format=image.format, size=list(image.size))
        except Exception as exc:  # noqa: BLE001, the summary must never fail the request
            info["decode_error"] = type(exc).__name__
        return info
    return {"type": str(kind), "keys": sorted(block)}


def plan(body: dict[str, Any]) -> tuple[str, Any]:
    results = _tool_results(body)
    names = {t.get("name") for t in body.get("tools") or [] if isinstance(t, dict)}
    summary = {
        "case": CASE.get("name"),
        "model": body.get("model"),
        "tools_offered": sorted(n for n in names if n),
        "messages": len(body.get("messages") or []),
        "body_json_chars": len(json.dumps(body)),
        "tool_results": [
            {
                "is_error": bool(r.get("is_error")),
                "content": [_describe(r["content"])]
                if isinstance(r.get("content"), str)
                else [_describe(b) for b in r.get("content") or []],
            }
            for r in results
        ],
    }
    REQUESTS.append(summary)
    if TOOL in names and len(results) < int(CASE.get("calls", 1)):
        return "tool_use", (TOOL, {"index": len(results)})
    return "text", "Stub: done."


stub.plan = plan


def jpeg(side: int, target_bytes: int, seed: int) -> bytes:
    """A side x side JPEG close to target_bytes: noise blended into a gradient, the amount of
    noise and the quality searched so the size lands within a few percent."""
    rng = random.Random(seed)
    base = Image.linear_gradient("L").resize((side, side)).convert("RGB")
    noise = Image.frombytes("RGB", (side, side), rng.randbytes(side * side * 3))
    best = b""
    for quality in (85, 95, 100):
        low, high = 0.0, 1.0
        for _ in range(18):
            alpha = (low + high) / 2
            buffer = io.BytesIO()
            Image.blend(base, noise, alpha).save(buffer, "JPEG", quality=quality)
            best = buffer.getvalue()
            if abs(len(best) - target_bytes) < target_bytes * 0.03:
                return best
            if len(best) < target_bytes:
                low = alpha
            else:
                high = alpha
    return best


def png(side: int, seed: int) -> bytes:
    rng = random.Random(seed)
    image = Image.linear_gradient("L").resize((side, side)).convert("RGB")
    for _ in range(40):
        x, y = rng.randrange(side - 60), rng.randrange(side - 20)
        image.paste((rng.randrange(255),) * 3, (x, y, x + 60, y + 20))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def image_block(data: bytes, mime: str, label: str) -> dict[str, Any]:
    encoded = base64.b64encode(data).decode()
    ORIGINALS[hashlib.sha256(encoded.encode()).hexdigest()] = label
    return {"type": "image", "data": encoded, "mimeType": mime}


def make_server() -> Any:
    @tool(
        "view_image",
        "Spike tool: returns the content blocks of the current case.",
        {
            "type": "object",
            "properties": {"index": {"type": "integer"}},
            "additionalProperties": False,
        },
    )
    async def view_image(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": CASE["blocks"](int(args.get("index", 0)))}

    return create_sdk_mcp_server(name="app", version="1.0.0", tools=[view_image])


def build_env(scratch: Path, base_url: str, extra: dict[str, str]) -> dict[str, str]:
    env = {name: "" for name in os.environ if name not in ENV_KEEP}
    env.update(
        {
            "HOME": str(scratch / "home"),
            "CLAUDE_CONFIG_DIR": str(scratch / "config"),
            "CLAUDE_CODE_TMPDIR": str(scratch / "tmp"),
            "TMPDIR": str(scratch / "tmp"),
            "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "ANTHROPIC_API_KEY": KEY,
            "ANTHROPIC_BASE_URL": base_url,
        }
    )
    env.update(extra)
    return env


def sdk_view(content: Any) -> list[dict[str, Any]]:
    """What the runner would see in the ToolResultBlock (types and sizes only)."""
    if isinstance(content, str):
        return [{"type": "str", "chars": len(content), "head": content[:300]}]
    out = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = str(block.get("text", ""))
            out.append({"type": "text", "chars": len(text), "head": text[:300]})
        elif isinstance(block, dict):
            out.append(
                {
                    "type": str(block.get("type")),
                    "keys": sorted(block),
                    "json_chars": len(json.dumps(block)),
                }
            )
        else:
            out.append({"type": type(block).__name__})
    return out


async def run_case(name: str, base_url: str, case: dict[str, Any]) -> dict[str, Any]:
    CASE.clear()
    CASE.update(case, name=name)
    first = len(REQUESTS)
    scratch = Path(tempfile.mkdtemp(prefix="g0-spike-"))
    for sub in ("home", "config", "tmp", "work"):
        (scratch / sub).mkdir()
    stderr: list[str] = []
    options = ClaudeAgentOptions(
        system_prompt="Spike. Call the tool once.",
        tools=[],
        allowed_tools=[TOOL],
        permission_mode="dontAsk",
        mcp_servers={"app": make_server()},
        strict_mcp_config=True,
        setting_sources=[],
        model="claude-opus-5-5",
        max_turns=8,
        cwd=str(scratch / "work"),
        env=build_env(scratch, base_url, case.get("env", {})),
        stderr=stderr.append,
        max_buffer_size=case.get("max_buffer_size"),
    )
    seen: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    started = time.monotonic()
    error = None
    files: list[str] = []
    try:
        async with asyncio.timeout(180):
            async with ClaudeSDKClient(options) as client:
                await client.query("View the image of this case.")
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, ToolUseBlock):
                                seen.append({"tool_use": block.name})
                    elif isinstance(message, UserMessage) and isinstance(
                        message.content, list
                    ):
                        for block in message.content:
                            if isinstance(block, ToolResultBlock):
                                extra = getattr(message, "tool_use_result", None)
                                seen.append(
                                    {
                                        "tool_result": {
                                            "is_error": bool(block.is_error),
                                            "content": sdk_view(block.content),
                                            "echoed_content_json_chars": len(
                                                json.dumps(block.content, default=str)
                                            ),
                                            "echoed_tool_use_result_json_chars": len(
                                                json.dumps(extra, default=str)
                                            )
                                            if extra
                                            else 0,
                                        }
                                    }
                                )
                    elif isinstance(message, ResultMessage):
                        result = {
                            "subtype": message.subtype,
                            "is_error": message.is_error,
                            "num_turns": message.num_turns,
                        }
    except Exception as exc:  # noqa: BLE001, every failure is a result of the spike
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        files = sorted(
            f"{p.relative_to(scratch)} ({p.stat().st_size} bytes)"
            for p in scratch.rglob("*")
            if p.is_file() and p.stat().st_size > 20_000
        )
        shutil.rmtree(scratch, ignore_errors=True)
    return {
        "case": name,
        "describes": case["describes"],
        "env": case.get("env", {}),
        "max_buffer_size": case.get("max_buffer_size") or "SDK default, 1048576",
        "seconds": round(time.monotonic() - started, 1),
        "error": error,
        "result": result,
        "sdk_side": seen,
        "requests_to_the_stub": REQUESTS[first:],
        "scratch_files_over_20kB_at_the_end": files,
        "stderr_lines": len(stderr),
    }


BIG = 64 * 1024 * 1024


def cases() -> dict[str, dict[str, Any]]:
    kb = {
        n: jpeg(1568, n * 1000, n)
        for n in (100, 200, 300, 380, 450, 600, 800, 1000, 3000)
    }
    j6m = jpeg(2000, 6_000_000, 4)
    wide = jpeg(4000, 1_500_000, 5)
    small = jpeg(784, 60_000, 7)
    p = png(1568, 6)
    print(
        "fixture bytes:",
        {
            **{f"jpeg_{n}k": len(d) for n, d in kb.items()},
            "jpeg_6m_2000px": len(j6m),
            "jpeg_4000px": len(wide),
            "jpeg_784px": len(small),
            "png_1568": len(p),
        },
        flush=True,
    )

    def one(data: bytes, mime: str, label: str) -> Any:
        return lambda index: [image_block(data, mime, f"{label}#{index}")]

    out: dict[str, dict[str, Any]] = {
        "text_small": {
            "describes": "control: a 2,000 character text result",
            "blocks": lambda i: [{"type": "text", "text": "a" * 2000}],
        },
        "png_1568": {
            "describes": "one 1568 px PNG",
            "blocks": one(p, "image/png", "png_1568"),
        },
    }
    # A. One 1568 px JPEG per size, SDK default buffer (what the runner has today).
    for n, data in kb.items():
        out[f"jpeg_{n}k"] = {
            "describes": f"one 1568 px JPEG of about {n} kB, SDK default buffer",
            "blocks": one(data, "image/jpeg", f"jpeg_{n}k"),
        }
    # B. The same with a 64 MiB SDK buffer: what the CLI does to the image itself.
    for n in (300, 380, 450, 600, 800, 1000, 3000):
        out[f"jpeg_{n}k_bigbuf"] = {
            "describes": f"one 1568 px JPEG of about {n} kB, max_buffer_size 64 MiB",
            "max_buffer_size": BIG,
            "blocks": one(kb[n], "image/jpeg", f"jpeg_{n}k"),
        }
    out["jpeg_6m_bigbuf"] = {
        "describes": "one 2000 px JPEG of about 6 MB (over the API's 5 MB per image), 64 MiB buffer",
        "max_buffer_size": BIG,
        "blocks": one(j6m, "image/jpeg", "jpeg_6m"),
    }
    out["jpeg_4000px"] = {
        "describes": "one 4000 px JPEG of about 1.5 MB, 64 MiB buffer",
        "max_buffer_size": BIG,
        "blocks": one(wide, "image/jpeg", "jpeg_4000px"),
    }
    # C. Several images.
    out["three_jpeg_100k_one_result"] = {
        "describes": "three 100 kB JPEGs and a text block in one tool result, default buffer",
        "blocks": lambda i: (
            [{"type": "text", "text": "three pages"}]
            + [image_block(kb[100], "image/jpeg", f"jpeg_100k#{n}") for n in range(3)]
        ),
    }
    out["three_jpeg_200k_one_result"] = {
        "describes": "three 200 kB JPEGs and a text block in one tool result, default buffer",
        "blocks": lambda i: (
            [{"type": "text", "text": "three pages"}]
            + [image_block(kb[200], "image/jpeg", f"jpeg_200k#{n}") for n in range(3)]
        ),
    }
    out["three_jpeg_200k_one_result_bigbuf"] = {
        "describes": "three 200 kB JPEGs and a text block in one tool result, 64 MiB buffer",
        "max_buffer_size": BIG,
        "blocks": lambda i: (
            [{"type": "text", "text": "three pages"}]
            + [image_block(kb[200], "image/jpeg", f"jpeg_200k#{n}") for n in range(3)]
        ),
    }
    out["five_calls_jpeg_200k"] = {
        "describes": "five tool calls in a row, one 200 kB JPEG each, default buffer",
        "calls": 5,
        "blocks": one(kb[200], "image/jpeg", "jpeg_200k"),
    }
    out["three_calls_jpeg_1000k_bigbuf"] = {
        "describes": "three tool calls in a row, one 1 MB JPEG each, 64 MiB buffer",
        "calls": 3,
        "max_buffer_size": BIG,
        "blocks": one(kb[1000], "image/jpeg", "jpeg_1000k"),
    }
    # D. MAX_MCP_OUTPUT_TOKENS: how an image counts, and what happens above the limit.
    for limit in (100, 1000, 1500, 1600, 1700, 1800, 1900, 2000, 3000, 6000):
        out[f"jpeg_200k_limit_{limit}"] = {
            "describes": f"one 1568 px 200 kB JPEG with MAX_MCP_OUTPUT_TOKENS={limit}",
            "env": {"MAX_MCP_OUTPUT_TOKENS": str(limit)},
            "blocks": one(kb[200], "image/jpeg", "jpeg_200k"),
        }
    for limit in (300, 900, 1500, 1600, 1700, 1800, 2000):
        out[f"jpeg_784px_limit_{limit}"] = {
            "describes": f"one 784 px 60 kB JPEG with MAX_MCP_OUTPUT_TOKENS={limit}",
            "env": {"MAX_MCP_OUTPUT_TOKENS": str(limit)},
            "blocks": one(small, "image/jpeg", "jpeg_784px"),
        }
    out["jpeg_1000k_limit_1000_bigbuf"] = {
        "describes": "one 1 MB JPEG with MAX_MCP_OUTPUT_TOKENS=1000, 64 MiB buffer",
        "env": {"MAX_MCP_OUTPUT_TOKENS": "1000"},
        "max_buffer_size": BIG,
        "blocks": one(kb[1000], "image/jpeg", "jpeg_1000k"),
    }
    out["eight_jpeg_100k_one_result_bigbuf"] = {
        "describes": "eight 100 kB JPEGs in one tool result, default limit, 64 MiB buffer",
        "max_buffer_size": BIG,
        "blocks": lambda i: [
            image_block(kb[100], "image/jpeg", f"jpeg_100k#{n}") for n in range(8)
        ],
    }
    for count in (12, 14, 15, 16, 20):
        out[f"{count}_jpeg_100k_one_result_bigbuf"] = {
            "describes": f"{count} JPEGs of 100 kB in one tool result, default limit, 64 MiB buffer",
            "max_buffer_size": BIG,
            "blocks": lambda i, count=count: [
                image_block(kb[100], "image/jpeg", f"jpeg_100k#{n}")
                for n in range(count)
            ],
        }
    # E. Text, for the read_document cap.
    out["text_30k"] = {
        "describes": "a 30,000 character text result (the read_document cap)",
        "blocks": lambda i: [{"type": "text", "text": "mot " * 7500}],
    }
    out["text_49k"] = {
        "describes": "a 49,000 character text result",
        "blocks": lambda i: [{"type": "text", "text": "mot " * 12250}],
    }
    out["text_120k"] = {
        "describes": "a 120,000 character text result, default limit",
        "blocks": lambda i: [{"type": "text", "text": "mot " * 30000}],
    }
    out["text_30k_limit_1000"] = {
        "describes": "a 30,000 character text result with MAX_MCP_OUTPUT_TOKENS=1000",
        "env": {"MAX_MCP_OUTPUT_TOKENS": "1000"},
        "blocks": lambda i: [{"type": "text", "text": "mot " * 7500}],
    }
    out["text_and_image_limit_1000"] = {
        "describes": "30,000 characters of text plus one 200 kB JPEG, MAX_MCP_OUTPUT_TOKENS=1000",
        "env": {"MAX_MCP_OUTPUT_TOKENS": "1000"},
        "blocks": lambda i: [
            {"type": "text", "text": "mot " * 7500},
            image_block(kb[200], "image/jpeg", "jpeg_200k"),
        ],
    }
    return out


async def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("result.json")
    only = set(sys.argv[2:])
    server = ThreadingHTTPServer(("127.0.0.1", 0), stub.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    cli_version = "unknown"
    with contextlib.suppress(ImportError):
        from claude_agent_sdk._cli_version import __cli_version__ as cli_version
    report: dict[str, Any] = {
        "sdk": claude_agent_sdk.__version__,
        "bundled_cli": cli_version,
        "platform": f"{sys.platform} {os.uname().machine}",
        "python": sys.version.split()[0],
        "pillow": Image.__version__ if hasattr(Image, "__version__") else "",
        "stub": str(STUB_PATH.relative_to(ROOT))
        if STUB_PATH.is_relative_to(ROOT)
        else "tests/anthropic_stub.py",
        "cases": [],
    }
    for name, case in cases().items():
        if only and name not in only:
            continue
        print("case", name, flush=True)
        entry = await run_case(name, base_url, case)
        report["cases"].append(entry)
        print(
            "  ",
            json.dumps({k: entry[k] for k in ("seconds", "error", "result")})[:260],
            flush=True,
        )
        for item in entry["sdk_side"]:
            if "tool_result" in item:
                tr = item["tool_result"]
                print(
                    "   sdk echo chars:",
                    tr["echoed_content_json_chars"],
                    "+",
                    tr["echoed_tool_use_result_json_chars"],
                    flush=True,
                )
        for request in entry["requests_to_the_stub"]:
            for tr in request["tool_results"]:
                print("   ->", json.dumps(tr)[:420], flush=True)
        out.write_text(json.dumps(report, indent=1) + "\n")
    server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
