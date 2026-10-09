"""Synthetic engine data folders (data/jobs.csv and data/jobs/<id>/) for the importer tests."""

import csv
import io
from pathlib import Path
from typing import Any

from app.jobs.importer import ENGINE_COLUMNS

PDF = b"%PDF-1.7\n% synthetic\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def row(**values: str) -> dict[str, str]:
    """One CSV row with every engine column, empty unless given."""
    base = {column: "" for column in ENGINE_COLUMNS}
    base.update(
        {
            "id": "acme-ai-engineer",
            "company": "Acme",
            "title": "AI Engineer",
            "location": "Paris",
            "type": "CDI",
            "url": "https://jobs.ashbyhq.com/acme/1",
            "found_on": "2026-10-07",
            "status": "found",
        }
    )
    base.update(values)
    return base


def csv_text(rows: list[dict[str, str]], columns: list[str] | None = None) -> str:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, columns or list(ENGINE_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for values in rows:
        writer.writerow(values)
    return out.getvalue()


def posting(company: str = "Acme", title: str = "AI Engineer", **header: str) -> str:
    fields = {
        "COMPANY": company,
        "TITLE": title,
        "LOCATION": "Paris",
        "URL": "https://jobs.ashbyhq.com/acme/1",
        "PUBLISHED": "2026-10-01",
    }
    fields.update(header)
    head = "".join(f"{key}: {value}\n" for key, value in fields.items())
    return f"{head}\nAbout the role\n\nBuild agents.\n"


def write_data(
    root: Path,
    rows: list[dict[str, str]],
    files: dict[str, dict[str, Any]] | None = None,
    *,
    columns: list[str] | None = None,
    bom: bool = False,
) -> Path:
    """root/data with jobs.csv and, per id, the files given ({"cv.pdf": bytes or str})."""
    data = root / "data"
    (data / "jobs").mkdir(parents=True, exist_ok=True)
    text = csv_text(rows, columns)
    (data / "jobs.csv").write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    for job_id, contents in (files or {}).items():
        folder = data / "jobs" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        for name, content in contents.items():
            raw = content.encode("utf-8") if isinstance(content, str) else content
            (folder / name).write_bytes(raw)
    return data
