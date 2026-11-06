"""The fake backend's steps kept for one prompt (`if_prompt`, `unless_prompt`), and One click
Apply's recorded stream (apply A19 and A20, `apply_agent.json`): a run whose first message lacks
the answer never asks (A20): it types its own and names it as chosen; a run with the answer types
it; with review_first it names the send (`apply_ready`); with gate_clicked it only reads."""

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


def test_the_apply_agent_stream_never_asks_and_uses_the_answer_it_has() -> None:
    chose = tools(ASKED)
    names = [name for name, _ in chose]
    assert "browser_ask_person" not in names
    assert ("browser_type", {"ref": "e22", "text": "One month"}) in chose
    assert chose[-1][0] == "apply_done"
    assert chose[-1][1]["outcome"] == "submitted"
    assert chose[-1][1]["chosen"][0]["field"] == "What is your notice period?"
    for prompt in (ANSWERED, SAVED):
        made = tools(prompt)
        names = [name for name, _ in made]
        assert "browser_ask_person" not in names
        assert ("browser_type", {"ref": "e22", "text": "Six weeks"}) in made
        assert names[-1] == "apply_done"
        assert made[-1][1]["outcome"] == "submitted"
        # Every call is one of the agent's own tools (an unknown name would be denied).
        assert set(names) <= set(applier.TOOLS)
    # The same page, the same refs, up to the notice period.
    assert tools(ANSWERED)[:9] == chose[:9]


def test_review_first_names_the_send_and_after_the_gate_only_reads() -> None:
    review = tools(json.dumps({"review_first": True, "saved_answers": {"q": "Six weeks"}}))
    names = [name for name, _ in review]
    assert names[-1] == "apply_ready"
    assert review[-1][1]["submit_ref"] == "e23"
    assert ("browser_click", {"ref": "e23"}) not in review
    assert "apply_done" not in names
    gate = tools(json.dumps({"gate_clicked": True, "review_first": True, "posting": {}}))
    assert [name for name, _ in gate] == ["browser_wait_for", "browser_snapshot", "apply_done"]
    assert set(names) | {"apply_done"} <= set(applier.TOOLS)
