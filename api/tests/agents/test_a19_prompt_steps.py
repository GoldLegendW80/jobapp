"""The fake backend's steps kept for one prompt (`if_prompt`, `unless_prompt`), and One click
Apply's recorded stream (apply A19, `apply_agent.json`): its first run, whose first message lacks
the person's answer, asks and stops; the run after the answer types it and reports submitted."""

from __future__ import annotations

import json

from app.agents import fake_backend
from app.apply import applier

ASKED = json.dumps({"answers_to_your_questions": [], "saved_answers": {}})
ANSWERED = json.dumps(
    {"answers_to_your_questions": [{"field": "What is your notice period?", "answer": "Six weeks"}]}
)
SAVED = json.dumps({"saved_answers": {"what is your notice period": "Six weeks"}})


def test_a_step_is_kept_by_what_the_prompt_holds() -> None:
    plain = {"sleep": 0.1}
    has, lacks = {**plain, "if_prompt": "Six"}, {**plain, "unless_prompt": "Six"}
    assert fake_backend.kept(plain, "")
    assert fake_backend.kept(plain, "Six")
    assert fake_backend.kept(has, "Six weeks")
    assert not fake_backend.kept(has, "Four weeks")
    assert fake_backend.kept(lacks, "Four weeks")
    assert not fake_backend.kept(lacks, "Six weeks")


def tools(prompt: str) -> list[tuple[str, dict]]:
    made = []
    for step in fake_backend.load_script(applier.NAME):
        if "message" in step and fake_backend.kept(step, prompt):
            for block in step["message"].get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    made.append((block["name"].removeprefix("mcp__app__"), block["input"]))
    return made


def test_the_apply_agent_stream_asks_then_uses_the_answer() -> None:
    asked = tools(ASKED)
    assert asked[-1] == (
        "browser_ask_person",
        {
            "reason": "The form asks for the notice period, which the profile does not hold.",
            "field": "What is your notice period?",
            "kind": "fact",
        },
    )
    assert "apply_done" not in [name for name, _ in asked]
    for prompt in (ANSWERED, SAVED):
        made = tools(prompt)
        names = [name for name, _ in made]
        assert "browser_ask_person" not in names
        assert ("browser_type", {"ref": "e22", "text": "Six weeks"}) in made
        assert names[-1] == "apply_done"
        assert made[-1][1]["outcome"] == "submitted"
        # Every call is one of the agent's own tools (an unknown name would be denied).
        assert set(names) <= set(applier.TOOLS)
    # The first run's calls are the second's first ones: the same page, the same refs.
    assert tools(ANSWERED)[: len(asked) - 1] == asked[:-1]
