"""D1 spike, throwaway: page 1 of each PDF by both engines side by side, the font check, the table.

    python compare.py <out dir> <evidence dir>

Reads <out>/weasy-*.json, <out>/chromium-*.json and the PDFs the benches wrote. Writes to the
evidence dir: `<fixture>.<layout>.png` (WeasyPrint left, Chromium right, each at the step its own
fit loop chose), `probes-<n>.png` (one row per CSS feature), and `results.json`.

The font check is the loud failure of a missing font, engine agnostic: every font a PDF embeds
must be one of the families its HTML asks for (EXPECTED); any other font means a fallback.
"""

import json
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

LABEL = ImageFont.load_default()
EXPECTED = {
    "edge-missing-font": ("TplMissing",),
    "edge-data-font": ("Tpl",),
}  # else DejaVu


def font_check(name: str, embedded: list[str]) -> str:
    expected = EXPECTED.get(name, ("DejaVu",))
    wrong = [f for f in embedded if not f.startswith(expected)]
    return "FAIL: not asked for: " + ", ".join(wrong) if wrong else "pass"


def fonts(pdf: Path) -> list[str]:
    names = set()
    for page in PdfReader(pdf).pages:
        resources = page.get("/Resources") or {}
        for ref in (resources.get("/Font") or {}).values():
            names.add(
                str(ref.get_object().get("/BaseFont", "?")).lstrip("/").split("+")[-1]
            )
    return sorted(names)


def page1(pdf: Path, width: int) -> Image.Image:
    doc = pdfium.PdfDocument(str(pdf))
    page = doc[0]
    image = page.render(scale=width / page.get_width()).to_pil().convert("RGB")
    doc.close()
    return image


def pair(
    left: Image.Image, right: Image.Image, title_l: str, title_r: str
) -> Image.Image:
    h = max(left.height, right.height) + 18
    out = Image.new("RGB", (left.width + right.width + 12, h), (255, 255, 255))
    draw = ImageDraw.Draw(out)
    draw.text((4, 3), title_l, fill=(0, 0, 0), font=LABEL)
    draw.text((left.width + 16, 3), title_r, fill=(0, 0, 0), font=LABEL)
    out.paste(left, (0, 18))
    out.paste(right, (left.width + 12, 18))
    draw.line((left.width + 6, 0, left.width + 6, h), fill=(180, 0, 0), width=2)
    return out


def diff(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute difference of two greyscale renders at the same size, 0 to 255."""
    a, b = a.convert("L"), b.convert("L").resize(a.size)
    return round(
        sum(abs(x - y) for x, y in zip(a.tobytes(), b.tobytes(), strict=True))
        / (a.width * a.height),
        2,
    )


def main(out: Path, evidence: Path) -> None:
    evidence.mkdir(parents=True, exist_ok=True)
    weasy = json.loads((out / "weasy-alone.json").read_text())
    chrome = json.loads((out / "chromium-alone.json").read_text())
    results: dict = {"fixtures": {}, "probes": {}, "edges": {}}
    for name, w in weasy["fit_per_child"].items():
        c = chrome["fit"][name]
        sw = w["first_fit"] if w["first_fit"] is not None else 7
        sc = c["first_fit"] if c["first_fit"] is not None else 7
        lw, lc = (
            out / "pdf-weasy" / f"{name}.s{sw}.pdf",
            out / "pdf-chromium" / f"{name}.s{sc}.pdf",
        )
        a, b = page1(lw, 600), page1(lc, 600)
        pair(
            a,
            b,
            f"WeasyPrint {weasy['cold'].get('version')}  step {sw}  pages {w['pages']}",
            f"Chromium {chrome.get('version')}  step {sc}  pages {c['pages']}",
        ).save(evidence / f"{name}.png", optimize=True)
        same = page1(out / "pdf-chromium" / f"{name}.s{sw}.pdf", 600)
        results["fixtures"][name] = {
            "weasy_step": sw,
            "chromium_step": sc,
            "weasy_pages": w["pages"],
            "chromium_pages": c["pages"],
            "diff_same_step": diff(a, same),
            "font_check_weasy": font_check(name, fonts(lw)),
            "font_check_chromium": font_check(name, fonts(lc)),
        }
    rows = []
    for name in weasy["probes"]:
        w, c = (
            out / "pdf-weasy" / f"probe-{name}.pdf",
            out / "pdf-chromium" / f"probe-{name}.pdf",
        )
        if not (w.exists() and c.exists()):
            results["probes"][name] = {
                "missing": [str(p.name) for p in (w, c) if not p.exists()]
            }
            continue
        a, b = page1(w, 360), page1(c, 360)
        rows.append(pair(a, b, f"{name}: WeasyPrint", "Chromium"))
        results["probes"][name] = {
            "diff": diff(a, b),
            "fonts_weasy": fonts(w),
            "fonts_chromium": fonts(c),
        }
    for start in range(0, len(rows), 8):
        chunk = rows[start : start + 8]
        sheet = Image.new(
            "RGB",
            (max(r.width for r in chunk), sum(r.height + 6 for r in chunk)),
            (230, 230, 230),
        )
        y = 0
        for row in chunk:
            sheet.paste(row, (0, y))
            y += row.height + 6
        sheet.save(evidence / f"probes-{start // 8 + 1}.png", optimize=True)
    for name in weasy["edges"]:
        if name in ("edge-outside.default-fetcher", "strict-limits"):
            continue
        entry = {
            "weasy": {
                k: weasy["edges"][name].get(k)
                for k in ("ok", "pages", "fetched", "stderr", "detail")
            },
            "chromium": {
                k: chrome["edges"][name].get(k)
                for k in ("ok", "pages", "fetched", "detail")
            },
        }
        for engine, folder in (("weasy", "pdf-weasy"), ("chromium", "pdf-chromium")):
            pdf = out / folder / f"{name}.pdf"
            if pdf.exists():
                entry[engine]["fonts"] = fonts(pdf)
                entry[engine]["font_check"] = font_check(name, entry[engine]["fonts"])
        results["edges"][name] = entry
        if name == "edge-missing-font":
            w, c = (
                out / "pdf-weasy" / f"{name}.pdf",
                out / "pdf-chromium" / f"{name}.pdf",
            )
            if w.exists() and c.exists():
                pair(
                    page1(w, 600), page1(c, 600), "missing font: WeasyPrint", "Chromium"
                ).save(evidence / f"{name}.png", optimize=True)
    for name in ("edge-outside.default-fetcher", "strict-limits"):
        results["edges"][name] = weasy["edges"][name]
    (evidence / "results.json").write_text(
        json.dumps(results, indent=1, ensure_ascii=False)
    )
    print(json.dumps({k: len(v) for k, v in results.items()}))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
