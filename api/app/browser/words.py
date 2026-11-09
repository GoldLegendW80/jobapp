"""The finder's folding of a text into its words (`words`), in a module that imports no agent:
the posting reader (app/jobs/reader.py) folds as the finder does (app/browser/finder.py) without
registering the finder's agents, whose day long wall time would become the agents queue's."""

import unicodedata


def words(text: str) -> str:
    """A text as its words, case and accents aside, any other character a space: how a saved
    title is looked for in an address's query (`FinderSession._note_address`)."""
    plain = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in plain).split())
