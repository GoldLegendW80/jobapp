"""The person's writing rules that code enforces (SPEC-documents.md, Decision 9).

Stored in `document_settings` (D5), read and saved by the api (`GET` and `PUT
/api/documents/settings`), and handed to the pure checks as this frozen value. Off by default
for a new user: no dash rule, no banned phrase. Rules only a reader can judge stay in the
profile's `facts` section and reach the writer as text.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.documents import rules

BANNED_PHRASES_MAX = 50
BANNED_PHRASE_MAX = 100

Phrase = Annotated[str, StringConstraints(min_length=1, max_length=BANNED_PHRASE_MAX)]


class DocumentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    # An em dash, an en dash or a spaced hyphen used as a separator becomes a comma.
    no_dash_separators: bool = False
    # Whole words, compared with case and accents folded; a sentence holding one is corrected.
    banned_phrases: Annotated[tuple[Phrase, ...], Field(max_length=BANNED_PHRASES_MAX)] = ()

    @field_validator("banned_phrases", mode="before")
    @classmethod
    def _list_is_tuple(cls, value: object) -> object:
        # JSON has no tuple: a list from the api or the row is taken as it is.
        return tuple(value) if isinstance(value, list) else value

    @field_validator("banned_phrases")
    @classmethod
    def _clean(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Stripped, blank ones dropped, the same phrase once (folded), in the order given."""
        seen: set[str] = set()
        out: list[str] = []
        for phrase in value:
            text = " ".join(phrase.split())
            key = rules.fold(text).text
            if not key.strip() or key in seen:
                continue
            seen.add(key)
            out.append(text)
        return tuple(out)


DEFAULT = DocumentSettings()
