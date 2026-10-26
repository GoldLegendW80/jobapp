"""A deterministic model recording, resolved only from the previous observed snapshot."""

import json
import re
import time
import uuid

from claude_agent_sdk._internal.message_parser import parse_message

from app.agents.fake_backend import FakeBackend

STEPS = [
    ("browser_snapshot", {}),
    ("browser_type", {"ref": ("searchbox", "Query"), "text": "robot"}),
    ("browser_select_option", {"ref": ("combobox", "Remote"), "values": ["1"]}),
    ("browser_click", {"ref": ("button", "Search")}),
    ("browser_click", {"ref": ("link", "Next page")}),
    ("browser_navigate", {"url": "/b13/jobs/robot"}),
    (
        "report_postings",
        {
            "postings": [
                {"title": "Robot Engineer", "url": "/b13/jobs/robot", "text": "Invented model text"}
            ]
        },
    ),
]


class Recording(FakeBackend):
    """Plays `steps`; `obeys_page` is a hostile model whose calls are expected to be refused."""

    def __init__(self, steps=STEPS, obeys_page=False):
        super().__init__(speed=0)
        self.steps, self.obeys_page = steps, obeys_page
        self.results = []

    async def stream(self, request):
        variables = {"session_id": str(uuid.uuid4()), "model": request.options.model}
        snapshot = ""
        for index, (name, arguments) in enumerate(self.steps):
            args = dict(arguments)
            if isinstance(args.get("ref"), tuple):
                role, label = args["ref"]
                match = re.search(rf'{role} "{label}"[^\n]*\[ref=(e\d+)\]', snapshot)
                assert match, (name, snapshot)
                args["ref"] = match[1]
            block = {
                "type": "tool_use",
                "name": "mcp__app__" + name,
                "id": f"call_{index}",
                "input": args,
            }
            yield parse_message(
                {
                    "type": "assistant",
                    "session_id": variables["session_id"],
                    "message": {
                        "id": f"msg_{index}",
                        "model": variables["model"],
                        "role": "assistant",
                        "content": [block],
                        "usage": {"input_tokens": 100, "output_tokens": 20},
                    },
                }
            )
            result = await self._call(request, block)
            self.results.append(result)
            assert self.obeys_page or not result["is_error"], result
            text = result["content"][0]["text"]
            try:
                payload = json.loads(text)
            except ValueError:
                payload = {}  # a plain refusal sentence
            snapshot = payload.get("untrusted_page_snapshot", snapshot)
            yield parse_message(
                {
                    "type": "user",
                    "session_id": variables["session_id"],
                    "message": {"role": "user", "content": [result]},
                }
            )
        yield self._result(variables, time.monotonic(), len(self.steps), "success", False)
