"""How a term list of the profile matches text. The jobs module scores and filters with this, and
the importer relies on it to convert the engine's regular expressions without changing a single
decision.

A term list is `{terms, advanced_regex}` (schemas.TermList, Category, Gate). It matches a text
when any of its terms does, case-insensitive:

- plain term (advanced_regex false): the words as written, as a whole word, with an optional
  plural ending "s" or "es". "agent" matches "Agent" and "agents", never "agentic" or "reagent".
  This is exactly what engine/jobs.py `_rx` does with the terms of profile/skills.json.
- advanced term (advanced_regex true): a Python regular expression searched anywhere in the text,
  as written, with nothing added. This is what engine/jobs.py does with the title, exclude and
  location regexes of engine/boards.json and with the gates of profile/skills.json.

`compile_terms` builds one pattern for the whole list, an alternation tried in list order, so
`findall` on it counts occurrences the way the engine counts a skills category.
"""

import re
from collections.abc import Sequence

PLURAL = r"(?:s|es)?"
# Characters that mean something in a regular expression outside a character class. Spaces,
# hyphens and accents stay as they are, so a converted term remains readable.
_META = re.compile(r"([.^$*+?{}\[\]\\|()])")


def escape(text: str) -> str:
    """`text` as a regular expression that matches it literally, escaping only what must be."""
    return _META.sub(r"\\\1", text)


def plain_pattern(term: str) -> str:
    """The pattern a plain term stands for: whole word, optional plural."""
    return rf"(?<!\w){escape(term)}{PLURAL}(?!\w)"


def compile_terms(terms: Sequence[str], advanced_regex: bool) -> re.Pattern[str] | None:
    """One case-insensitive pattern for the list, or None for an empty list (matches nothing)."""
    if not terms:
        return None
    if advanced_regex:
        source = "|".join(f"(?:{term})" for term in terms)
    else:
        source = rf"(?<!\w)(?:{'|'.join(escape(term) for term in terms)}){PLURAL}(?!\w)"
    return re.compile(source, re.IGNORECASE)


def matches(terms: Sequence[str], advanced_regex: bool, text: str) -> bool:
    pattern = compile_terms(terms, advanced_regex)
    return pattern is not None and pattern.search(text) is not None
