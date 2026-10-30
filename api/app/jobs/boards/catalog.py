"""Which company boards a search reads, and the starter list Find jobs offers.

`company_boards` in the search section (profile/schemas.CompanyBoard) is the person's list. None
means never saved: Find jobs then shows the starter list (`seed`) ticked as a proposal, saved
only once the person changes it or keeps it (a Save of other settings leaves the key out: the
release before refuses it, SPEC-jobs, Company job boards, Rollback). A sweep reads only a saved
list: None reads no board, so no account starts calling the ATSs before its person chose.

The starter list (seed.json): the 20 boards that answered on 2026-10-30 in the research of
alternatives to the deleted engine, and the 13 boards of the engine's own list that also
answered that day, one request each (Lakera left out: a security company, CLAUDE.md rule 2;
Pigment left out: not on Recruitee). This module imports nothing of the sweep, so app/jobs/sweep.py
can import it.
"""

import json
from functools import cache
from pathlib import Path

from app.profile.schemas import CompanyBoard, SearchSection

SEED = Path(__file__).with_name("seed.json")


@cache
def _seed() -> tuple[CompanyBoard, ...]:
    data = json.loads(SEED.read_text(encoding="utf-8"))
    return tuple(CompanyBoard.model_validate(board) for board in data["boards"])


def seed() -> list[CompanyBoard]:
    return list(_seed())


def api_host(ats: str, board: str) -> str:
    """The host a board's API answers on (ats.api_host, without importing the readers)."""
    from app.jobs.boards.ats import api_host as host

    return host(ats, board)


def enabled_boards(search: SearchSection | None) -> list[CompanyBoard]:
    """The saved boards a sweep of `search` reads, in their saved order."""
    if search is None or search.company_boards is None:
        return []
    return [board for board in search.company_boards if board.enabled]
