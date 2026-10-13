"""The merge of an ingestion run (a stub of G10, replaced by G12).

`engine.advance` calls `run` once, under the run's row lock, in the transaction that closes the
run, after it has deleted the items of a replaced text. The real merge (G12) reads the run's
items (`reuse.source_steps` with `stored_items.of_stored_text()`, never `tools`: this module
is imported by the api, whose image holds no Claude Agent SDK), turns them into proposals and
answers how many it wrote. Until then a run ends `done` with zero proposals.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.ingestion.runs_models import IngestionRun


def run(db: Session, ingestion_run: IngestionRun, now: datetime) -> int:
    """Turn the run's items into proposals and answer how many were written. The caller holds
    the run's row lock and commits. The stub writes none."""
    del db, ingestion_run, now
    return 0
