"""The approval editor's limits, with the document letter's browser typing boundary."""

import re

ANSWER_MAX = 5000
LETTER_MAX = 10_000  # browser/browserd/tools/policy.py, TEXT_MAX
# Browser actions allow LF in a multiline box, but refuse every other C0/C1 control and DEL.
LETTER_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")


def letter_issue(value: str) -> str | None:
    if len(value) > LETTER_MAX:
        return "letter_too_long"
    if LETTER_CONTROL.search(value):
        return "letter_control_character"
    return None
