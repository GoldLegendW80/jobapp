"""The answer bank (SPEC-apply, "Answers to standard questions", source 4): every answer a person
typed or corrected in a review, kept with its question and reused when the same question comes
back. This module is the pure part: the normalised question key, the lookup and the update on a
person's edit. The table that stores it is the apply tables' (A2); a bank belongs to one person
and `Context` refuses a bank of another. The bank never overrides a policy: `answers.resolve`
applies the EEO policy, the salary stance and its never answered fields (someone else's details,
age, citizenship, current pay, free text) before it reads the bank.
"""

import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

KEY_MAX = 200
VALUE_MAX = 5000


def question_key(label: str) -> str:
    """The key of a question: lower case, accents and punctuation gone, required marks and
    "(optional)" dropped, spaces collapsed. "Prénom *" and "prenom" are one question."""
    text = unicodedata.normalize("NFD", (label or "").lower().replace("\u2019", "'"))
    text = re.sub("[̀-ͯ]", "", text)
    text = re.sub(r"\((?:required|optional|obligatoire|facultatif)\)|\*|✱", " ", text)
    text = re.sub(r"[^a-z0-9+']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:KEY_MAX]


@dataclass(frozen=True)
class AnswerBank:
    """One person's answers by question key. Immutable: `with_answer` returns the new bank."""

    user_id: uuid.UUID
    entries: Mapping[str, str] = field(default_factory=dict)

    def lookup(self, label: str) -> str | None:
        key = question_key(label)
        return self.entries.get(key) if key else None

    def with_answer(self, label: str, value: str | None) -> "AnswerBank":
        """The bank after the person typed `value` for this question; an empty value forgets it.
        A question with no key (a label of punctuation only) changes nothing."""
        key = question_key(label)
        if not key:
            return self
        entries = dict(self.entries)
        text = (value or "").strip()[:VALUE_MAX]
        if text:
            entries[key] = text
        else:
            entries.pop(key, None)
        return AnswerBank(self.user_id, entries)
