"""Build the recorded ATS fixtures of tests/jobs/fixtures/ats/ (run by hand, once; not a test).

    curl -o <raw>/ashby-mistral.ai.json https://api.ashbyhq.com/posting-api/job-board/mistral.ai
    ... (ashby openai, greenhouse anthropic and databricks with ?content=true, lever palantir and
    sonarsource with ?mode=json)
    uv run python -m tests.jobs.build_ats_fixtures <raw> <job folder holding engine/>

Writes, from the six real boards:
- <ats>-<org>.json: the board body cut down to a few postings (some the engine keeps, some it
  leaves out), HTML-only fields emptied, e-mail addresses replaced by jobs@example.org. Titles,
  locations and descriptions are public job ads: no personal data.
- expected.json: what engine/jobs.py `fetch` itself yields for each cut-down body (the engine's
  functions are extracted with `ast` and run, nothing else of the engine is executed), for the
  parser parity test.
- filter_cases.json: every posting of the six full boards as (org, title, location, published),
  and the engine's keep decisions with engine/boards.json's regexes: by the terms alone, and
  with the 30 day window on TODAY. The filter parity test runs the app's converted terms on the
  same postings.
"""

import ast
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent / "fixtures" / "ats"
BOARDS = [
    ("ashby", "mistral.ai", "Mistral AI"),
    ("ashby", "openai", "OpenAI"),
    ("greenhouse", "anthropic", "Anthropic"),
    ("greenhouse", "databricks", "Databricks"),
    ("lever", "palantir", "Palantir"),
    ("lever", "sonarsource", "Sonar"),
]
TODAY = "2026-10-09"
KEEP = 6
COLUMNS = ["org", "title", "location", "published", "keep_terms", "keep_30_days"]
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
HTML_FIELDS = {"descriptionHtml", "description", "opening", "additional", "descriptionBody"}


def engine_functions(engine: Path) -> dict[str, Any]:
    tree = ast.parse((engine / "jobs.py").read_text())
    wanted = [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {"fetch", "plain"}
    ]
    namespace: dict[str, Any] = {"html": html, "re": re, "dt": dt, "sys": sys}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), "engine/jobs.py", "exec"), namespace)  # noqa: S102
    return namespace


def scrub(value: Any) -> Any:
    if isinstance(value, str):
        return EMAIL.sub("jobs@example.org", value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {
            k: ("" if k in HTML_FIELDS and isinstance(v, str) else scrub(v))
            for k, v in value.items()
        }
    return value


def main(raw: Path, job_root: Path) -> None:
    engine = engine_functions(job_root / "engine")
    cfg = json.loads((job_root / "engine" / "boards.json").read_text())
    want, skip, where = (
        re.compile(cfg[k], re.I) for k in ("title_re", "exclude_re", "location_re")
    )
    per_board = {b["org"]: b for b in cfg["boards"]}
    since = (dt.date.fromisoformat(TODAY) - dt.timedelta(days=30)).isoformat()
    expected: dict[str, Any] = {}
    cases = []
    for ats, org, company in BOARDS:
        body = json.loads((raw / f"{ats}-{org}.json").read_text())
        board = {"ats": ats, "org": org, "company": company}
        posts = list(engine["fetch"](lambda _url, body=body: body, board))
        skip_b = per_board.get(org, {}).get("exclude_re")
        skip_b = re.compile(skip_b, re.I) if skip_b else None
        for title, loc, _url, pub, _text in posts:
            terms = bool(
                want.search(title)
                and not skip.search(title)
                and not (skip_b and skip_b.search(title))
                and where.search(loc)
            )
            cases.append([org, title, loc, pub, terms, terms and pub >= since])
        jobs = body["jobs"] if isinstance(body, dict) else body
        kept = [
            j
            for j, p in zip(_listed(ats, jobs), posts, strict=True)
            if want.search(p[0]) and where.search(p[1])
        ]
        others = [j for j in _listed(ats, jobs) if j not in kept]
        chosen = (
            sorted(kept, key=_size)[: KEEP // 2]
            + sorted(others, key=_size)[: KEEP - min(len(kept), KEEP // 2)]
        )
        small = scrub({**body, "jobs": chosen} if isinstance(body, dict) else chosen)
        if isinstance(small, dict) and "meta" in small:
            small["meta"] = {"total": len(chosen)}
        (HERE / f"{ats}-{org}.json").write_text(
            json.dumps(small, ensure_ascii=False, indent=1) + "\n"
        )
        expected[f"{ats}-{org}"] = [
            list(p) for p in engine["fetch"](lambda _url, small=small: small, board)
        ]
    (HERE / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=1) + "\n")
    lines = ",\n".join(json.dumps(case, ensure_ascii=False) for case in cases)
    head = json.dumps({"today": TODAY, "days": 30, "columns": COLUMNS})[:-1]
    (HERE / "filter_cases.json").write_text(f'{head}, "cases": [\n{lines}\n]}}\n')
    print(
        f"{len(cases)} filter cases, {sum(c[4] for c in cases)} kept by the terms, "
        f"{sum(c[5] for c in cases)} within 30 days"
    )


def _listed(ats: str, jobs: list[Any]) -> list[Any]:
    return [j for j in jobs if not (ats == "ashby" and j.get("isListed") is False)]


def _size(job: dict[str, Any]) -> int:
    return len(json.dumps(job))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
