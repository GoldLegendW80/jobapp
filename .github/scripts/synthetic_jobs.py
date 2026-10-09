"""A made-up job folder for the CI e2e run of web/e2e/jobs.spec.ts: never Harald's data.

    python3 .github/scripts/synthetic_jobs.py <out dir>

Writes <out dir>/jobs.csv and <out dir>/jobs/<id>/ in the engine's layout (the input of
`python -m app.jobs.cli convert --source-dir`), shaped like the counts the spec asserts:
433 rows with the status counts of CSV_COUNTS in web/e2e/jobs.spec.ts, some rows unscored, the
applied rows with a posting.txt and a cv.pdf, and some but not all applied titles holding
"engineer". Companies, titles and texts are invented; every URL is on example.org.
Change STATUS_COUNTS here when the spec's counts change.
"""

import csv
import json
import sys
from pathlib import Path

# web/e2e/jobs.spec.ts CSV_COUNTS, in the engine's status vocabulary.
STATUS_COUNTS = {
    "found": 158,
    "shortlisted": 146,
    "cv_ready": 3,
    "staged": 29,
    "applied": 29,
    "interview": 2,
    "rejected": 0,
    "closed": 6,
    "skipped": 60,
}
TITLES = (
    "AI Engineer",
    "Product Manager, Agents",
    "Forward Deployed Engineer",
    "Solutions Architect",
    "Applied Scientist",
    "Developer Advocate",
)
CITIES = (
    "Paris, France",
    "Bordeaux, France",
    "London, UK",
    "Remote, Europe",
    "Berlin, Germany",
)
COLUMNS = [
    "id",
    "company",
    "title",
    "location",
    "type",
    "url",
    "found_on",
    "status",
    "applied_on",
    "fit",
    "competencies",
    "gaps",
    "description",
    "jd",
    "cv",
    "letter",
    "report",
    "notes",
]


def tiny_pdf(text: str) -> bytes:
    """A valid one-page PDF with one line of text, cross-reference offsets computed."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def main(out: Path) -> None:
    (out / "jobs").mkdir(parents=True, exist_ok=True)
    rows = []
    n = 0
    for status, count in STATUS_COUNTS.items():
        for i in range(count):
            n += 1
            company = f"Example Company {n:03d}"
            if status == "applied":
                # Every third applied title has no "engineer" anywhere, so a search narrows it.
                title = (
                    "Product Manager, Agents" if i % 3 == 0 else "Applied AI Engineer"
                )
            else:
                title = TITLES[n % len(TITLES)]
            job_id = f"example-company-{n:03d}-{title.lower().split(',')[0].replace(' ', '-')}"
            fit = "" if n % 17 == 0 else str((n * 7) % 101)
            row = {
                "id": job_id,
                "company": company,
                "title": title,
                "location": CITIES[n % len(CITIES)],
                "type": "CDI",
                "url": f"https://jobs.example.org/{job_id}",
                "found_on": f"2026-09-{1 + n % 28:02d}",
                "status": status,
                "applied_on": "2026-10-01"
                if status in {"applied", "interview"}
                else "",
                "fit": fit,
                "competencies": "agents, evaluation",
                "gaps": "years" if n % 5 == 0 else "",
                "description": f"Synthetic posting {n} for the CI run.",
                "jd": "",
                "cv": "",
                "letter": "",
                "report": "",
                "notes": "",
            }
            folder = out / "jobs" / job_id
            if status in {"applied", "interview", "staged"}:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "posting.txt").write_text(
                    f"COMPANY: {company}\nTITLE: {title}\nLOCATION: {row['location']}\n"
                    f"URL: {row['url']}\nPUBLISHED: 2026-09-01\n\n"
                    f"{company} is a made-up company.\n\nThe role: {title}, also made up.\n",
                    encoding="utf-8",
                )
                (folder / "cv.pdf").write_bytes(tiny_pdf(f"Synthetic CV {n}"))
                (folder / "cv.json").write_text(
                    json.dumps({"target": title, "headline": "Synthetic"}),
                    encoding="utf-8",
                )
                row["cv"] = f"data/jobs/{job_id}/cv.pdf"
                row["jd"] = f"data/jobs/{job_id}/posting.txt"
            rows.append(row)
    with (out / "jobs.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} synthetic rows in {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: synthetic_jobs.py <out dir>")
    main(Path(sys.argv[1]))
