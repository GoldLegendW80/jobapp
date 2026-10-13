"""What a reader of `ingestion_items` outside a step needs (the merge of G12, the run's page).

The merge runs inside `engine.advance`, and `advance` also runs in the api process (after a
start, a resume, a continue or a cancel; a run whose every step is reused is merged there). The
api image holds no Claude Agent SDK, so nothing here may import `app.ingestion.tools` or any
module that builds a tool: `test_the_merge_path_imports_without_the_sdk` imports this module and
`merge` with the SDK absent. `tools` offers the same three names for the code of a step.
"""

from typing import Any

from app.ingestion import items
from app.ingestion.runs_models import IngestionItem
from app.ingestion.runs_models import of_stored_text as of_stored_text


def as_item(row: IngestionItem) -> items.Item:
    """The stored row as the item G5's `item_proposals.propose` takes (origin `agent`, the
    default of `items.build`). The stored value is the cleaned one, so the key and the hash come
    out as stored."""
    return items.build(row.section, row.key, row.action, row.value)


def as_citations(row: IngestionItem) -> list[dict[str, Any]]:
    """The stored citations with their document, as `item_proposals.propose` takes them:
    `{document_id, excerpt, start, end}`. The merge adds `transcribed` where it applies."""
    return [
        {
            "document_id": str(row.document_id),
            "excerpt": one["excerpt"],
            "start": one["start"],
            "end": one["end"],
        }
        for one in row.citations
    ]
