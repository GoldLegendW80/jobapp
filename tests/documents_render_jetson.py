"""The driver of tests/documents_render_jetson.sh (documents D3), run in a container of the
worker's image next to the renderer, on an internal network of their own, on the Jetson.

    python /d3/documents_render_jetson.py [renders]

Prints the command line's build table for D1's fixtures through the real fit loop and the real
client (`file, step, pages, status`, with the renders each fit made and its duration), then makes
`renders` renders (50 by default) of the typical CV and prints the worker side's resident memory
before and after them, as one line of JSON at the end. The renderer's memory is read from its
cgroup by the shell script.
"""

import asyncio
import json
import resource
import sys
import time

sys.path[:0] = ["/d3", "/app"]

from app.documents import fit, render_client
from tests.documents import render_html

LIMITS = {"timeout_s": 30, "max_pdf_bytes": 4 * 1024 * 1024, "at_once": 1}


def rss_kib() -> int:
    with open("/proc/self/status") as status:
        return next(
            int(line.split()[1]) for line in status if line.startswith("VmRSS:")
        )


async def render(html: str) -> render_client.Rendered:
    return await render_client.render(html, **LIMITS)


async def main(renders: int) -> None:
    print(f"{'file':<14}{'step':>5}{'pages':>6}  {'status':<9}{'renders':>8}{'ms':>7}")
    for name in render_html.FIXTURES:
        started = time.monotonic()
        fitted = await fit.fit(
            render_html.STEPS, lambda s, n=name: render_html.build(n, s), render=render
        )
        ms = int((time.monotonic() - started) * 1000)
        status = "OK" if fitted.fit else "overflow"
        print(
            f"{name:<14}{fitted.step:>5}{fitted.pages:>6}  {status:<9}{fitted.renders:>8}{ms:>7}"
        )
    html = render_html.build("cv-typical", 1)
    before = rss_kib()
    walls = []
    for _ in range(renders):
        started = time.monotonic()
        assert (await render(html)).pages == 1
        walls.append(int((time.monotonic() - started) * 1000))
    walls.sort()
    print(
        json.dumps(
            {
                "renders": renders,
                "worker_rss_kib_before": before,
                "worker_rss_kib_after": rss_kib(),
                "worker_peak_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "ms_median": walls[len(walls) // 2],
                "ms_max": walls[-1],
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 50))
