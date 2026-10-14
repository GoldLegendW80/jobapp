"""D1 spike, throwaway: Playwright's Chromium headless shell, measured the way D3 would run it.

    python chromium_bench.py <html dir> <out dir> <full|quick|sandbox> <tag> [--no-sandbox]

One warm browser, a fresh context per render with JavaScript disabled, every request but data:
aborted and recorded (route), `page.pdf(A4, print_background, prefer_css_page_size)`. `sandbox`
mode only launches the browser (Chromium's own sandbox on, which Playwright turns off unless
chromium_sandbox=True) and renders one page, to record whether it starts under the stack's settings.
"""

import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from memwatch import Watch
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

ARGS = ["--disable-dev-shm-usage", "--disable-gpu"]


class Renderer:
    def __init__(self, browser):
        self.browser = browser

    def render(self, html: Path, out: Path | None) -> dict:
        seen: list = []
        t0 = time.monotonic()
        context = self.browser.new_context(java_script_enabled=False, offline=True)

        def route(r):
            seen.append({"url": r.request.url[:120], "outcome": "aborted"})
            r.abort()

        context.route("**/*", route)
        page = context.new_page()
        try:
            page.set_content(html.read_text(), wait_until="load", timeout=30000)
            pdf = page.pdf(
                format="A4", print_background=True, prefer_css_page_size=True
            )
        except Exception as error:  # noqa: BLE001  (a spike records any failure)
            context.close()
            return {
                "ok": False,
                "detail": f"{type(error).__name__}: {str(error)[:300]}",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            }
        context.close()
        if out is not None:
            out.write_bytes(pdf)
        pages = len(PdfReader(io.BytesIO(pdf)).pages)
        return {
            "ok": True,
            "pages": pages,
            "wall_ms": int((time.monotonic() - t0) * 1000),
            "fetched": seen,
        }


def fit(r: Renderer, html: Path, name: str, pdfs: Path) -> dict:
    with Watch() as w:
        t0 = time.monotonic()
        steps = [
            r.render(html / f"{name}.s{step}.html", pdfs / f"{name}.s{step}.pdf")
            for step in range(8)
        ]
        total = time.monotonic() - t0
    return {
        "total_ms": int(total * 1000),
        "pages": [s.get("pages") for s in steps],
        "ok": all(s["ok"] for s in steps),
        "container_peak": w.peak_mib,
        "first_fit": next(
            (i for i, s in enumerate(steps) if s.get("pages") == 1), None
        ),
    }


def main(html: Path, out: Path, mode: str, tag: str, no_sandbox: bool) -> None:
    pdfs = out / "pdf-chromium"
    pdfs.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "engine": "chromium",
        "tag": tag,
        "chromium_sandbox": not no_sandbox,
    }
    with sync_playwright() as p:
        with Watch() as w:
            t0 = time.monotonic()
            try:
                browser = p.chromium.launch(
                    headless=True,
                    chromium_sandbox=not no_sandbox,
                    args=ARGS,
                    timeout=30000,
                )
            except Exception as error:  # noqa: BLE001  (a spike records any failure)
                text = f"{type(error).__name__}: {error}"
                report["launch"] = {
                    "ok": False,
                    "detail": text[:200] + " [...] " + text[-700:],
                }
                (out / f"chromium-{tag}.json").write_text(json.dumps(report, indent=1))
                print(json.dumps(report["launch"])[:600])
                return
            launch_ms = int((time.monotonic() - t0) * 1000)
            r = Renderer(browser)
            first = r.render(html / "cv-typical.two.s0.html", None)
            cold_ms = int((time.monotonic() - t0) * 1000)
        report["version"] = browser.version
        report["launch"] = {"ok": True, "launch_ms": launch_ms}
        report["cold"] = {**first, "wall_ms": cold_ms, "container_peak": w.peak_mib}
        report["container_base"] = w.base_mib
        if mode != "sandbox":
            report["warm"] = [
                r.render(html / "cv-typical.two.s0.html", None) for _ in range(5)
            ]
            index = json.loads((html / "index.json").read_text())
            names = (
                index["fixtures"]
                if mode == "full"
                else ["cv-typical.two", "cv-long.two"]
            )
            report["fit"] = {name: fit(r, html, name, pdfs) for name in names}
            if mode == "full":
                report["edges"] = {
                    name: r.render(html / f"{name}.html", pdfs / f"{name}.pdf")
                    for name in index["edges"]
                }
                report["probes"] = {
                    name: r.render(
                        html / f"probe-{name}.html", pdfs / f"probe-{name}.pdf"
                    )
                    for name in index["probes"]
                }
        browser.close()
    (out / f"chromium-{tag}.json").write_text(json.dumps(report, indent=1))
    print(
        json.dumps(
            {
                "launch": report["launch"],
                "cold_ms": report["cold"]["wall_ms"],
                "peak": report["cold"]["container_peak"],
            }
        )
    )


if __name__ == "__main__":
    main(
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        sys.argv[3],
        sys.argv[4],
        "--no-sandbox" in sys.argv,
    )
