"""A private form fill may lower its remaining turn budget, never raise the definition cap."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agents import runner
from app.apply import form_agent


@pytest.mark.parametrize(
    ("remaining", "expected"), [(None, 30), (1, 1), (17, 17), (30, 30), (100, 30)]
)
def test_sdk_options_lower_only_turn_budget(tmp_path, remaining, expected):
    async def gate(*args):
        return {}

    tools = SimpleNamespace(allowed_names=[], server={})
    options = runner.build_options(
        form_agent.APPLY_FORM, tools, {}, tmp_path, gate, turn_budget=remaining
    )
    assert options.max_turns == expected


@pytest.mark.parametrize("remaining", [0, -1, True, "3"])
def test_invalid_sdk_budget_refused_before_spawn(tmp_path, remaining):
    with pytest.raises(ValueError, match="positive integer"):
        runner.build_options(
            form_agent.APPLY_FORM, SimpleNamespace(), {}, tmp_path, None, turn_budget=remaining
        )


def test_private_input_defaults_compatibly_and_rejects_zero():
    data = {"request": "a" * 32, "token_budget": 100}
    assert form_agent.FormInput.model_validate(data).turn_budget == 30
    with pytest.raises(ValidationError):
        form_agent.FormInput.model_validate({**data, "turn_budget": 0})


def test_exhausted_budget_does_not_start_runtime():
    def context():
        raise AssertionError("An exhausted profile budget cannot start a model")

    agent = form_agent.RuntimeFormAgent(context)
    assert (
        asyncio.run(agent(SimpleNamespace(token_budget=1, turn_budget=0))).stopped == "turn_limit"
    )
