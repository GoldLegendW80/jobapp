"""D1 spike, throwaway (deleted by D3): WeasyPrint as a kind of the parser sandbox.

Copied beside app/ingestion/sandbox/main.py in the d1spike-weasy image, whose main.py gains the
one line `"render_pdf": "render_pdf",` in HANDLERS (the only change; LIMITS stay the default
512 MiB of address space, 45 s of CPU, no file growth, 16 descriptors, no new process or thread).
It is imported only after main.py's lockdown, like every handler.

Standard input: one HTML document, or with {"many": true} a JSON list of them (one child for a
whole fit loop, the alternative to one child per step). Options: {"fetcher": "refuse"} (default:
every address but data: is refused and recorded) or "default" (WeasyPrint's own fetcher, to show
what the seccomp filter does to it). The answer carries the PDFs in base64, the page count and the
time of each render, the addresses refused, and the error text when a render fails (a spike may
say why; the real kind will answer codes only).
"""

import base64
import json
import time

_KEEP = 120


def _fetcher(mode: str, seen: list):
    from weasyprint.urls import default_url_fetcher

    def fetch(url, *args, **kwargs):
        if url.startswith("data:"):
            return default_url_fetcher(url, *args, **kwargs)
        if mode != "default":
            seen.append({"url": url[:_KEEP], "outcome": "refused"})
            raise ValueError("address refused")
        try:
            return default_url_fetcher(url, *args, **kwargs)
        except Exception as error:
            seen.append(
                {
                    "url": url[:_KEEP],
                    "outcome": f"{type(error).__name__}: {str(error)[:_KEEP]}",
                }
            )
            raise

    return fetch


def handle(data: bytes, options: dict, budget) -> dict:
    started = time.monotonic()
    import weasyprint

    imported_ms = int((time.monotonic() - started) * 1000)
    pages = json.loads(data) if options.get("many") else [data.decode("utf-8")]
    seen: list = []
    fetch = _fetcher(options.get("fetcher", "refuse"), seen)
    renders = []
    for page in pages:
        t0 = time.monotonic()
        try:
            document = weasyprint.HTML(string=page, url_fetcher=fetch).render()
            pdf = document.write_pdf()
        except Exception as error:  # noqa: BLE001  (a spike records any failure)
            return {
                "ok": False,
                "failure": "file",
                "code": "render_error",
                "detail": f"{type(error).__name__}: {error}"[:400],
            }
        renders.append(
            {
                "pages": len(document.pages),
                "ms": int((time.monotonic() - t0) * 1000),
                "pdf": base64.b64encode(pdf).decode(),
            }
        )
    return {
        "ok": True,
        "import_ms": imported_ms,
        "renders": renders,
        "fetched": seen,
        "weasyprint": weasyprint.__version__,
    }
