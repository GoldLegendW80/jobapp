"""The measured spec of an uploaded PDF CV, for the design writer (task D28; SPEC-documents.md,
"Design capture of an upload", step 3). Pure: no file, no log.

    measured(design, faces, texts) -> {"styles", "headings", "pages"}

- `styles`: the runs clustered by family (as `faces` maps them, font_measure), weight, italic,
  size (pt), letter spacing (em), capitals and colour, most characters first, STYLES_MAX at
  most; each with its line pitch in pt (the median of its runs' `pitch`), the x where its lines
  start (the most common, pt), its count of characters and a guessed role: `name` (the largest,
  in the top third of page 0), `headline` (the next one up there), `section heading` (capitals,
  larger than the body, or heavier and larger; short lines met twice or more),
  `body` (the most characters), `entry title` (the body's size, heavier), `small` (smaller
  than the body), else `meta line`.
- `headings`: the texts of the runs of the section heading styles, in reading order, as `texts`
  gives them (scrubbed: design_capture.scrub, a replaced text left out), HEADINGS_MAX at most, LABEL_MAX characters each:
  what a template's `labels` may print (design_capture.labels_of accepts nothing else).
- `pages`: per page the background, the four margins, the rules (thickness, colour, extent),
  the filled boxes (colour, corner radius), the bullet dots (diameter, colour, x), the photo
  boxes and their shape, every length in pt.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from statistics import median
from typing import Any

from app.documents import font_map
from app.ingestion.parser_client import DesignPdf

STYLES_MAX = 24
HEADINGS_MAX = 20
LABEL_MAX = 40
SHAPES_SHOWN = 60


def _pt(tenths: float) -> float:
    return round(tenths / 10, 1)


def _style_key(run, faces: Sequence[font_map.Mapped]) -> tuple:
    face = faces[run.font] if run.font < len(faces) else None
    family = face.css_name if face else None
    return (
        family,
        face.weight if face else 400,
        face.italic if face else False,
        round(run.size / 5) / 2,  # pt, to the half point
        round(run.spacing / 10) / 100,  # em, to the hundredth
        run.caps,
        run.colour,
    )


def _roles(styles: list[dict], page_height: float) -> None:
    if not styles:
        return
    body = max(styles, key=lambda s: s["characters"])
    body["role"] = "body"
    top = [s for s in styles if s["top_pt"] <= page_height / 3 and s is not body]
    by_size = sorted(top, key=lambda s: -s["size_pt"])
    if by_size and by_size[0]["size_pt"] > body["size_pt"]:
        by_size[0]["role"] = "name"
        if len(by_size) > 1 and by_size[1]["size_pt"] >= body["size_pt"]:
            by_size[1]["role"] = "headline"
    for style in styles:
        if style.get("role"):
            continue
        heavier = style["weight"] > body["weight"]
        larger = style["size_pt"] > body["size_pt"] + 0.5
        marked = style["caps"] or larger or (heavier and style["size_pt"] > body["size_pt"])
        short = style["lines"] >= 2 and style["longest"] <= LABEL_MAX
        if marked and short and style["size_pt"] >= body["size_pt"]:
            style["role"] = "section heading"
            continue
        if style["size_pt"] < body["size_pt"] - 0.4:
            style["role"] = "small"
        elif heavier:
            style["role"] = "entry title"
        else:
            style["role"] = "meta line"


def measured(
    design: DesignPdf, faces: Sequence[font_map.Mapped], texts: Sequence[Sequence[str]]
) -> dict[str, Any]:
    """The measured spec (see the module docstring); `texts[p][i]` is the scrubbed text of run
    i of page p."""
    clusters: dict[tuple, list] = defaultdict(list)
    for p, page in enumerate(design.read):
        for i, run in enumerate(page.runs):
            clusters[_style_key(run, faces)].append((p, i, run))
    styles = []
    for key, found in clusters.items():
        family, weight, italic, size, spacing, caps, colour = key
        starts = Counter(_pt(run.box[0]) for _, _, run in found)
        pitches = [run.pitch for _, _, run in found if run.pitch]
        styles.append(
            {
                "family": family,
                "weight": weight,
                "italic": italic,
                "size_pt": size,
                "letter_spacing_em": spacing,
                "caps": caps,
                "colour": colour,
                "line_pitch_pt": _pt(median(pitches)) if pitches else None,
                "x_pt": [x for x, _ in starts.most_common(3)],
                "top_pt": min(_pt(run.box[1]) for p, _, run in found if p == found[0][0]),
                "characters": sum(len(run.text) for _, _, run in found),
                "lines": len(found),
                "longest": max(len(run.text) for _, _, run in found),
                "_runs": [(p, i) for p, i, _ in found],
            }
        )
    styles.sort(key=lambda s: (-s["characters"], -s["size_pt"]))
    styles = styles[:STYLES_MAX]
    height = _pt(design.read[0].height) if design.read else 842.0
    _roles(styles, height)
    headings: list[str] = []
    marked = sorted((p, i) for s in styles if s["role"] == "section heading" for p, i in s["_runs"])
    for p, i in marked:
        text = texts[p][i].strip() if p < len(texts) and i < len(texts[p]) else ""
        if text != design.read[p].runs[i].text.strip():
            continue  # a text the secret rules replaced is no heading the model may name
        if 1 <= len(text) <= LABEL_MAX and text not in headings and len(headings) < HEADINGS_MAX:
            headings.append(text)
    for style in styles:
        del style["_runs"], style["longest"]
    pages = [_page(page) for page in design.read]
    return {"styles": styles, "headings": headings, "pages": pages}


def _page(page) -> dict[str, Any]:
    rules = [s for s in page.shapes if s.type == "rule"][:SHAPES_SHOWN]
    fills = [s for s in page.shapes if s.type == "fill"][:SHAPES_SHOWN]
    rounds = page.rounds or (False,) * len(page.images)
    return {
        "page": page.index,
        "background": page.background,
        "margins_pt": [_pt(m) for m in page.margins] if page.margins else None,
        "rules": [
            {
                "thickness_pt": _pt(s.width),
                "colour": s.colour,
                "box_pt": [_pt(v) for v in s.box],
            }
            for s in rules
        ],
        "boxes": [
            {"fill": s.colour, "radius_pt": _pt(s.radius), "box_pt": [_pt(v) for v in s.box]}
            for s in fills
        ],
        "dots": [
            {"diameter_pt": _pt(d.box[2] - d.box[0]), "colour": d.colour, "x_pt": _pt(d.box[0])}
            for d in page.dots[:SHAPES_SHOWN]
        ],
        "photos": [
            {"box_pt": [_pt(v) for v in box], "shape": "round" if found else "square"}
            for box, found in zip(page.images, rounds, strict=False)
        ],
    }
