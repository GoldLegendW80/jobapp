"""The chat history budget and the prompt layout (app/agents/chat.py), pure functions: the token
estimate, which turns are kept (always the newest unbroken tail, oldest first), the deterministic
note for the ones left out, and the prompt's order."""

import uuid

import pytest

from app.agents import chat
from app.agents.models import MessageRole

U, A = MessageRole.user, MessageRole.assistant


def turns(*texts: str) -> list[chat.Turn]:
    return [chat.Turn(role=U if i % 2 == 0 else A, text=text) for i, text in enumerate(texts)]


@pytest.mark.parametrize(
    ("text", "tokens"),
    [("", 4), ("abcd", 5), ("abcde", 6), ("x" * 4000, 1004), ("é" * 8, 6), ("👋" * 3, 5)],
)
def test_estimate_tokens(text: str, tokens: int) -> None:
    assert chat.estimate_tokens(text) == tokens


def test_empty_history() -> None:
    assert chat.fit_history([], 30_000) == chat.History(turns=[], omitted=0)


def test_everything_fits() -> None:
    history = chat.fit_history(turns("one", "two", "three"), 30_000)
    assert [t.text for t in history.turns] == ["one", "two", "three"]
    assert [t.role for t in history.turns] == [U, A, U]
    assert history.omitted == 0


def test_keeps_the_newest_tail_within_the_budget() -> None:
    # Each 400 character turn costs 104 tokens: a 300 token budget keeps the last two.
    long = turns(*(f"{i}" + "x" * 399 for i in range(10)))
    history = chat.fit_history(long, 300)
    assert [t.text[0] for t in history.turns] == ["8", "9"]
    assert history.omitted == 8
    assert sum(chat.estimate_tokens(t.text) for t in history.turns) <= 300


def test_budget_exactly_reached() -> None:
    history = chat.fit_history(turns("a" * 384, "b" * 384), 200)
    assert len(history.turns) == 2
    assert history.omitted == 0
    history = chat.fit_history(turns("a" * 384, "b" * 384), 199)
    assert [t.text[0] for t in history.turns] == ["b"]
    assert history.omitted == 1


def test_a_newest_turn_too_big_drops_everything_before_it() -> None:
    # Never a gap: once one turn does not fit, nothing older is kept, even if it would fit.
    history = chat.fit_history(turns("small", "y" * 10_000, "tiny"), 100)
    assert [t.text for t in history.turns] == ["tiny"]
    assert history.omitted == 2


def test_zero_budget() -> None:
    assert chat.fit_history(turns("a", "b"), 0) == chat.History(turns=[], omitted=2)


def test_a_long_thread_at_the_default_budget() -> None:
    # 400 turns of 2000 characters (504 tokens each): 59 fit into 30k tokens.
    long = turns(*(f"{i:04d}" + "z" * 1996 for i in range(400)))
    history = chat.fit_history(long, chat.HISTORY_BUDGET_TOKENS)
    assert len(history.turns) == 59
    assert history.omitted == 341
    assert history.turns[0].text.startswith("0341")
    assert history.turns[-1].text.startswith("0399")


def test_omitted_note_is_deterministic() -> None:
    assert chat.omitted_note(1).startswith("[1 earlier message is left out")
    assert chat.omitted_note(12).startswith("[12 earlier messages are left out")
    assert chat.omitted_note(3) == chat.omitted_note(3)


def test_first_message_prompt() -> None:
    job = uuid.UUID("00000000-0000-4000-8000-000000000001")
    prompt = chat.render_prompt(job, chat.History([], 0), "Does it fit?")
    assert prompt == (
        f"Job id: {job}\n\n"
        "This is the first message of the conversation.\n\n"
        'The user\'s new message:\n<message role="user">\nDoes it fit?\n</message>'
    )


def test_prompt_with_history_in_order_and_note() -> None:
    history = chat.History(turns=turns("first question", "first answer"), omitted=4)
    prompt = chat.render_prompt(None, history, "second question")
    assert "Job id" not in prompt
    note = prompt.index("[4 earlier messages are left out")
    first = prompt.index('<message role="user">\nfirst question\n</message>')
    answer = prompt.index('<message role="assistant">\nfirst answer\n</message>')
    new = prompt.index("The user's new message:")
    assert note < first < answer < new
    assert prompt.endswith('<message role="user">\nsecond question\n</message>')
    assert "first message of the conversation" not in prompt


def test_prompt_when_everything_was_left_out() -> None:
    prompt = chat.render_prompt(None, chat.History([], 7), "hello")
    assert "[7 earlier messages are left out" in prompt
    assert "<conversation>" in prompt


def test_chat_prompt_repr_hides_the_text() -> None:
    built = chat.ChatPrompt(text="secret life story", included=2, omitted=1, current="hi there")
    assert "secret" not in repr(built)
    assert "hi there" not in repr(built)
