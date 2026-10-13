"""GET /api/ingestion/prompt: the laptop-analysis prompt, filled for the caller (SPEC-ingestion.md,
"Laptop-analysis prompt"; tasks/ingestion/todo.md, G3).

The user copies the prompt from step 1 of /profile/fill and runs it with Claude Code or Codex on
their own computer. That assistant writes one Markdown file, the dossier, which the user reads and
then adds on the same page. This app never reads the user's computer and runs nothing on it: the
route only hands out text.

- The text lives in `prompts/laptop_dossier.md`: a front matter with `PROMPT_VERSION`, one
  preamble for each assistant, then the body they share. Changing the file without raising the
  version fails a test (tests/ingestion/test_prompt.py pins the hash of each version).
- The answer holds the two filled texts, the version and the SHA-256 of the file. The version is
  also written by the prompt into the dossier's first line (`dossier-format: <n>`), so a later
  step can tell which prompt a dossier came from.
- The only thing of the caller in the text is their display name, made safe for one line
  (`clean_name`). Nothing else of the account is in the answer: no email, no id.
- Like every route under /api/ingestion, the answer is `Cache-Control: no-store` (the middleware
  of routers.py).
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut

TEMPLATE_PATH = Path(__file__).parent / "prompts" / "laptop_dossier.md"

# Where the assistant writes the dossier, and the only place it may write.
OUTPUT_FOLDER = "~/jobapp-dossier"
# A name is one line of the prompt: longer than this it is cut.
NAME_MAX = 80
# What the prompt says in place of a name the account does not have.
NAME_UNKNOWN = "not given here, so ask me for my full name before you start"

ASSISTANTS = ("claude_code", "codex")

_FRONT_MATTER = re.compile(r"\A---\nPROMPT_VERSION: (\d+)\n---\n")
_MARKER = re.compile(r"^<!-- ([a-z_:]+) -->\n", re.MULTILINE)
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
_PLACEHOLDERS = frozenset({"user_name", "output_folder", "version"})
# The two invisible characters that spell words in some scripts (Persian, Hindi): kept in a name.
_JOINERS = "\u200c\u200d"


@dataclass(frozen=True)
class Template:
    version: int
    sha256: str
    preambles: dict[str, str]
    body: str


def parse(raw: bytes) -> Template:
    """The template file as its parts. Raises ValueError on a file this module cannot serve,
    so a broken edit fails at the first request and in the tests, never silently."""
    text = raw.decode("utf-8")
    front = _FRONT_MATTER.match(text)
    if front is None:
        raise ValueError("the prompt must start with a front matter holding PROMPT_VERSION")
    pieces = _MARKER.split(text[front.end() :])
    if pieces[0].strip():
        raise ValueError("the prompt has text before its first marker")
    parts = dict(zip(pieces[1::2], (piece.strip() for piece in pieces[2::2]), strict=True))
    expected = {f"preamble:{assistant}" for assistant in ASSISTANTS} | {"body"}
    if set(parts) != expected or len(pieces) != 2 * len(expected) + 1:
        raise ValueError(f"the prompt must hold exactly these parts: {sorted(expected)}")
    if not all(parts.values()):
        raise ValueError("a part of the prompt is empty")
    for name, part in parts.items():
        unknown = set(_PLACEHOLDER.findall(part)) - _PLACEHOLDERS
        if unknown:
            raise ValueError(f"unknown placeholder in the prompt ({name}): {sorted(unknown)}")
    return Template(
        version=int(front.group(1)),
        sha256=hashlib.sha256(raw).hexdigest(),
        preambles={assistant: parts[f"preamble:{assistant}"] for assistant in ASSISTANTS},
        body=parts["body"],
    )


@cache
def template() -> Template:
    """The shipped template, read once for the life of the process."""
    return parse(TEMPLATE_PATH.read_bytes())


def clean_name(name: str) -> str:
    """A display name as one short line of plain text: control and invisible characters dropped,
    every run of white space (a newline included) made one space, cut at NAME_MAX characters.
    Quotes, braces, backticks and Markdown stay as typed: the name is written once, on a line of
    its own, and the text is filled in a single pass, so nothing in a name is read as a
    placeholder. Empty when nothing is left."""
    spaced = "".join(" " if char.isspace() else char for char in name)
    visible = "".join(
        char
        for char in spaced
        if char in _JOINERS or not unicodedata.category(char).startswith("C")
    )
    return " ".join(visible.split())[:NAME_MAX].strip()


def fill(assistant: str, name: str) -> str:
    """The prompt for one assistant, with the caller's name, the output folder and the version."""
    shipped = template()
    values = {
        "user_name": clean_name(name) or NAME_UNKNOWN,
        "output_folder": OUTPUT_FOLDER,
        "version": str(shipped.version),
    }
    text = f"{shipped.preambles[assistant]}\n\n{shipped.body}\n"
    # One pass over the template: what a value holds is never looked at again.
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], text)


class PromptOut(BaseModel):
    version: int = Field(
        description="PROMPT_VERSION of the template, also the dossier-format the prompt asks for"
    )
    sha256: str = Field(description="SHA-256 of the template file, as shipped")
    output_folder: str = Field(description="The folder the prompt has the dossier written in")
    claude_code: str = Field(description="The prompt to paste into Claude Code")
    codex: str = Field(description="The prompt to paste into Codex")


router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])
CurrentUser = Annotated[User, Depends(deps.current_user)]


@router.get(
    "/prompt",
    response_model=PromptOut,
    responses={401: {"model": ErrorOut, "description": "Not signed in"}},
)
def get_prompt(user: CurrentUser) -> PromptOut:
    shipped = template()
    return PromptOut(
        version=shipped.version,
        sha256=shipped.sha256,
        output_folder=OUTPUT_FOLDER,
        claude_code=fill("claude_code", user.name),
        codex=fill("codex", user.name),
    )
