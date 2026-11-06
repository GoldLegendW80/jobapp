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
  gives them (scrubbed: design_capture.scrub, a replaced text left out), HEADINGS_MAX at most,
  LABEL_MAX characters each: what a template's `labels` may print (design_capture.labels_of
  accepts nothing else).
- `pages`: per page the background, the four margins, the rules (thickness, colour, extent),
  the filled boxes (colour, corner radius), the bullet dots (diameter, colour, x), the photo
  boxes and their shape, every length in pt.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from statistics import median
from typing import Any

from app.documents import font_map
from app.ingestion.parser_client import DesignPdf

# A contact line or a date, never a section heading's text (a heading left out falls back to
# the app's own label)
_NOT_A_HEADING = re.compile(r"[\d@/]")
WRAP_PITCH = 1.6  # of a heading's size: the most from one of its lines to the next
WRAP_EDGE = 10  # tenths of a point: the left edges of its lines
MAX_WRAPPED = 3  # lines of one heading
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


def _strength(style: dict) -> tuple:
    return (style["size_pt"], style["caps"], style["weight"])


def _roles(styles: list[dict]) -> None:
    if not styles:
        return
    body = max(styles, key=lambda s: s["characters"])
    body["role"] = "body"
    header = [s for s in styles if s["_header"] and s is not body]
    by_size = sorted(header, key=lambda s: -s["size_pt"])
    if by_size and by_size[0]["size_pt"] > body["size_pt"]:
        by_size[0]["role"] = "name"
        if len(by_size) > 1 and by_size[1]["size_pt"] >= body["size_pt"]:
            by_size[1]["role"] = "headline"
    candidates = []
    for style in styles:
        heavier = style["weight"] > body["weight"]
        larger = style["size_pt"] > body["size_pt"] + 0.5
        marked = style["caps"] or larger or (heavier and style["size_pt"] > body["size_pt"])
        short = style["lines"] >= 2 and style["longest"] <= LABEL_MAX
        below = not style["_header"]  # a heading recurs down the page, the header does not
        if style.get("role") or style["size_pt"] < body["size_pt"]:
            continue
        if marked and short and below:
            candidates.append(style)
    strongest = max(map(_strength, candidates), default=None)
    for style in styles:
        if style.get("role"):
            continue
        if style in candidates and _strength(style) == strongest:
            style["role"] = "section heading"
        elif style["size_pt"] < body["size_pt"] - 0.4:
            style["role"] = "small"
        elif style["weight"] > body["weight"]:
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
    header = design.read[0].height / 3 if design.read else 0.0  # tenths of a point
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
                "_header": all(p == 0 and run.box[1] <= header for p, _, run in found),
            }
        )
    styles.sort(key=lambda s: (-s["characters"], -s["size_pt"]))
    styles = styles[:STYLES_MAX]
    _roles(styles)
    headings: list[str] = []
    marked = sorted((p, i) for s in styles if s["role"] == "section heading" for p, i in s["_runs"])
    for group in _wrapped(design, marked):
        parts = [
            texts[p][i].strip() if p < len(texts) and i < len(texts[p]) else "" for p, i in group
        ]
        if any(
            part != design.read[p].runs[i].text.strip()
            for part, (p, i) in zip(parts, group, strict=True)
        ):
            continue  # a text the secret rules replaced is no heading the model may name
        text = " ".join(parts)
        if not 1 <= len(text) <= LABEL_MAX or text in headings or _NOT_A_HEADING.search(text):
            continue
        if len(headings) < HEADINGS_MAX:
            headings.append(text)
    for style in styles:
        del style["_runs"], style["longest"], style["_header"]
    pages = [_page(page) for page in design.read]
    return {"styles": styles, "headings": headings, "pages": pages}


def _continues(above, below) -> bool:
    """Whether `below` is the next line of a heading `above` wraps onto: the same font, size,
    colour and capitals (its letter spacing may read otherwise on a short word), the same left
    edge, its baseline within WRAP_PITCH of the size below."""
    drop = (below.baseline or below.box[3]) - (above.baseline or above.box[3])
    same = (above.font, above.size, above.colour, above.caps) == (
        below.font,
        below.size,
        below.colour,
        below.caps,
    )
    aligned = abs(below.box[0] - above.box[0]) <= WRAP_EDGE
    return same and aligned and 0 < drop <= WRAP_PITCH * above.size


def _wrapped(design: DesignPdf, marked: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """The heading runs, a heading wrapped onto lines below each other joined: the run next to
    one in the page's order that `_continues` it ("Key" over "Achievements", "Professional"
    over "Experience" in a narrow column), MAX_WRAPPED lines at most."""
    groups: list[list[tuple[int, int]]] = []
    taken: set[tuple[int, int]] = set()
    for p, i in marked:
        if (p, i) in taken:
            continue
        runs = design.read[p].runs
        group = [(p, i)]
        while group[0][1] > 0 and len(group) < MAX_WRAPPED:
            j = group[0][1] - 1
            if (p, j) in taken or not _continues(runs[j], runs[j + 1]):
                break
            group.insert(0, (p, j))
        while group[-1][1] + 1 < len(runs) and len(group) < MAX_WRAPPED:
            j = group[-1][1] + 1
            if not _continues(runs[j - 1], runs[j]):
                break
            group.append((p, j))
        taken.update(group)
        groups.append(group)
    return groups


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
