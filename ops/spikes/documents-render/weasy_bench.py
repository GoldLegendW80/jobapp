"""D1 spike, throwaway: WeasyPrint measured as a child of the parser sandbox.

    python weasy_bench.py <html dir> <out dir> <full|quick> <tag>

Run by tests/documents_render_jetson.sh in the d1spike-weasy image under the parser service's
settings of compose.yml (uid 10002, read only root, no capability, no new privilege, ipc none,
pids_limit 32, mem_limit 640m, no network). Each render is a child started exactly as
app/ingestion/sandbox/server.py starts one: `python -I -B .../sandbox/main.py <kind> <options>`,
empty environment, cwd /, close_fds, the document on standard input. One difference: standard
error is kept (server.py sends it to /dev/null) so that WeasyPrint's warnings are recorded.
This parent is not sandboxed (server.py is not either); it writes the PDFs to the mounted out dir.
"""

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from memwatch import Watch

CHILD = (sys.executable, "-I", "-B", "/app/app/ingestion/sandbox/main.py")


def child(kind: str, options: dict, data: bytes) -> dict:
    t0 = time.monotonic()
    proc = subprocess.run(
        [*CHILD, kind, json.dumps(options)],
        input=data,
        capture_output=True,
        env={},
        cwd="/",
        close_fds=True,
        timeout=60,
        check=False,
    )
    wall = time.monotonic() - t0
    try:
        answer = json.loads(proc.stdout) if proc.stdout else {}
    except ValueError:
        answer = {"unparsed": proc.stdout[:200].decode("utf-8", "replace")}
    warnings = sorted(
        {
            line.strip()[:160]
            for line in proc.stderr.decode("utf-8", "replace").splitlines()
            if line.strip()
        }
    )
    return {
        "exit": proc.returncode,
        "wall_ms": int(wall * 1000),
        "answer": answer,
        "stderr": warnings[:12],
    }


def render(html: Path, out: Path | None, **options) -> dict:
    result = child("render_pdf", options, html.read_bytes())
    answer = result.pop("answer")
    renders = answer.get("renders", [])
    if out is not None and renders:
        out.write_bytes(base64.b64decode(renders[0]["pdf"]))
    usage = answer.get("usage", {})
    return {
        **result,
        "ok": answer.get("ok"),
        "code": answer.get("code"),
        "detail": answer.get("detail"),
        "pages": renders[0]["pages"] if renders else None,
        "render_ms": renders[0]["ms"] if renders else None,
        "import_ms": answer.get("import_ms"),
        "hwm_mib": round(usage.get("max_rss_kb", 0) / 1024),
        "vmpeak_mib": round(usage.get("max_vm_kb", 0) / 1024),
        "fetched": answer.get("fetched"),
        "version": answer.get("weasyprint"),
    }


def fit_per_child(html: Path, name: str, pdfs: Path) -> dict:
    """The fit loop as D3 would run it with one child per render: all 8 steps, loosest first."""
    steps = []
    with Watch() as w:
        t0 = time.monotonic()
        for step in range(8):
            steps.append(
                render(html / f"{name}.s{step}.html", pdfs / f"{name}.s{step}.pdf")
            )
        total = time.monotonic() - t0
    return {
        "total_ms": int(total * 1000),
        "pages": [s["pages"] for s in steps],
        "hwm_mib": max(s["hwm_mib"] for s in steps),
        "ok": all(s["ok"] for s in steps),
        "container_peak": w.peak_mib,
        "first_fit": next((i for i, s in enumerate(steps) if s["pages"] == 1), None),
    }


def fit_one_child(html: Path, name: str) -> dict:
    """The alternative: one child renders the 8 steps (the import of WeasyPrint paid once)."""
    pages = [(html / f"{name}.s{step}.html").read_text() for step in range(8)]
    with Watch() as w:
        result = child("render_pdf", {"many": True}, json.dumps(pages).encode())
    answer = result.pop("answer")
    renders = answer.get("renders", [])
    return {
        "total_ms": result["wall_ms"],
        "import_ms": answer.get("import_ms"),
        "render_ms": [r["ms"] for r in renders],
        "pages": [r["pages"] for r in renders],
        "hwm_mib": round(answer.get("usage", {}).get("max_rss_kb", 0) / 1024),
        "ok": answer.get("ok"),
        "container_peak": w.peak_mib,
        "exit": result["exit"],
        "stderr": result["stderr"],
    }


def main(html: Path, out: Path, mode: str, tag: str) -> None:
    pdfs = out / "pdf-weasy"
    pdfs.mkdir(parents=True, exist_ok=True)
    index = json.loads((html / "index.json").read_text())
    report: dict = {"engine": "weasyprint", "tag": tag}
    if mode == "full":
        ping = child("ping", {}, b"d1")
        report["ping"] = {
            "exit": ping["exit"],
            "sandbox": ping["answer"].get("sandbox", ping["answer"]),
        }
    with Watch() as w:
        report["cold"] = render(html / "cv-typical.two.s0.html", None)
    report["cold"]["container_peak"] = w.peak_mib
    report["container_base"] = w.base_mib
    report["warm"] = [render(html / "cv-typical.two.s0.html", None) for _ in range(5)]
    names = index["fixtures"] if mode == "full" else ["cv-typical.two", "cv-long.two"]
    report["fit_per_child"] = {name: fit_per_child(html, name, pdfs) for name in names}
    report["fit_one_child"] = {
        name: fit_one_child(html, name) for name in ("cv-typical.two", "cv-long.two")
    }
    if mode == "full":
        report["edges"] = {
            name: render(html / f"{name}.html", pdfs / f"{name}.pdf")
            for name in index["edges"]
        }
        report["edges"]["edge-outside.default-fetcher"] = render(
            html / "edge-outside.html", None, fetcher="default"
        )
        # every limit of main.py as it is: Pango starts a thread, RLIMIT_NPROC 0 refuses it
        result = child(
            "render_pdf_strict", {}, (html / "cv-short.one.s0.html").read_bytes()
        )
        report["edges"]["strict-limits"] = {
            "exit": result["exit"],
            "wall_ms": result["wall_ms"],
            "stderr": result["stderr"],
            "ok": result["answer"].get("ok"),
        }
        report["probes"] = {
            name: render(html / f"probe-{name}.html", pdfs / f"probe-{name}.pdf")
            for name in index["probes"]
        }
    (out / f"weasy-{tag}.json").write_text(json.dumps(report, indent=1))
    print(
        json.dumps(
            {
                "cold_ms": report["cold"]["wall_ms"],
                "cold_hwm": report["cold"]["hwm_mib"],
                "ok": report["cold"]["ok"],
                "code": report["cold"]["code"],
                "detail": report["cold"]["detail"],
                "stderr": report["cold"]["stderr"][:3],
            }
        )
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4])
