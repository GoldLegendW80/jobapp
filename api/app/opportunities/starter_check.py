"""The live check of the starter catalog (opportunities O5b): every board of
`starter_catalog.json` asked once through the profile's own board check (app.profile.boards,
the same public posting API and the same answers as POST /api/profile/boards/check), before the
catalog ships. A board that does not answer with postings is taken out of the file by hand.

    cd api && uv run python -m app.opportunities.starter_check            # the shipped catalog
    cd api && uv run python -m app.opportunities.starter_check --file x   # another file

Prints one line per board (`ok  ashby:openai  42 postings`, or the error) and exits 1 when one
board failed. Tests run `check` with a fake transport only: no test reads a real board.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.opportunities import starter
from app.profile.boards import BoardChecker, BoardCheckOut


@dataclass(frozen=True)
class Checked:
    key: str
    answer: BoardCheckOut

    @property
    def ok(self) -> bool:
        return self.answer.exists and self.answer.error is None

    def line(self) -> str:
        if self.ok:
            return f"ok    {self.key}  {self.answer.postings} postings"
        return f"FAIL  {self.key}  {self.answer.error or 'not found'}"


async def check(boards: Sequence[starter.Board], checker: BoardChecker) -> list[Checked]:
    """Each board once, one after the other (the ATS hosts are asked at a person's pace)."""
    found: list[Checked] = []
    for board in boards:
        found.append(Checked(board.key, await checker.check(board.ats, board.org)))
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--file", type=Path, default=starter.CATALOG_PATH)
    args = parser.parse_args(argv)
    catalog = starter.load(args.file)
    results = asyncio.run(check(catalog.boards, BoardChecker(timeout_s=15.0)))
    for result in results:
        print(result.line())
    failed = [r for r in results if not r.ok]
    print(f"{len(results) - len(failed)} of {len(results)} boards answered")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover, the command line
    sys.exit(main())
