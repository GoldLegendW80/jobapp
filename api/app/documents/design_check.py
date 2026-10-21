"""The render check of a template a model wrote (task D25b; SPEC-documents.md, "Design capture
of an upload", step 4; issue #220).

`template_rules.check` is a static allow list: it proves which constructs a template holds, not
what the browser paints. Whether every field is truly shown is judged here, on the page the
renderer's own Chromium lays out (renderer/server.py, POST /check):

- the canary pages, two content shapes (SHAPES) of `_base/sample.json`'s invented CV and letter
  ("Alex Example"): the sample as it is, every list at two items or more, and `lone`, every
  list at one item (one employer with one bullet, one school, one language, one skill group of
  one item, one highlight, one competency, one paragraph) and every text at its hard limit, so
  a box sized for the sample's texts, or a rule that only a lone item meets, shows. Every text
  of the content and of the profile is led by a token of its own (`Q`, three letters, `Z`), so
  each needle names one field; the contact lines, the periods and the letter's date as printed.
  A grey photo when the template has a slot, so a photo laid over a text is seen too;
- each kind and shape at its loosest and its tightest step, laid out for print on a page as wide
  as an A4 page's text at the step's margin (`text_width`): `template_css` holds the template's
  `@page` to `size: A4` and `margin: var(--margin)`, so this is the page box print uses;
- for every glyph of every needle: it has a box (`hidden` otherwise), inside the A4 width and
  the measured height (`outside`), in a font of at least MIN_SIZE_PX (`small`), the element
  at its centre is the one that holds the text (`covered`: a box, a photo or a pseudo element
  of another element over it), and its pixels differ from the same page with that text painted
  transparent by at least CONTRAST to one, on MIN_PIXELS device pixels at least (`contrast`: a
  colour too close to the painted background, a pseudo element of its own element over it,
  something half transparent above it). A needle with no occurrence is `unprinted`;
- the tightest step must lay the sample CV and letter out shorter than the loosest
  (`density_unread`), so the fit loop can tighten the template.

Each page is its own call to the renderer, one page high or a little more: the renderer's memory
cap (compose.yml, 256 MiB) bounds what one check can lay out (renderer/server.py,
CHECK_MAX_HEIGHT). A renderer that cannot be reached, is busy or times out raises
CheckUnavailableError: nothing is known of the template, and no repair is spent.

Any problem refuses the template as a refusal of `template_rules` does: back to the model once,
then `classic` is kept. A Problem names a file, a field path and a step, never a text.

Luminance is read from the screenshots' 8 bit luma with the sRGB curve, an approximation of the
WCAG relative luminance per pixel; anti aliasing makes the edges of a glyph lighter, which is why
the bar is 3:1 on the strongest pixels and not 4.5:1 (the captured colours meet 4.5:1 by
construction, `template_rules.from_pdf`).
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from functools import cache
from typing import Any

from PIL import Image, ImageChops

from app.documents import content as c
from app.documents import photo as photos
from app.documents import profile_view, render_client, templates
from app.documents.render_client import Checked, RenderError
from app.documents.template_css import Problem

CODES = (
    "unprinted", "hidden", "outside", "small", "covered", "contrast", "density_unread", "render",
)  # fmt: skip
CONTRAST = 3.0
MIN_PIXELS = 2  # device pixels of one glyph at CONTRAST or more
MIN_SIZE_PX = 7.0  # 5.25 pt: the tightest body (7.6 pt) at 0.7 em still passes
EDGE_PX = 0.5
PROBLEMS_MAX = 24  # what one answer to the model lists
CSS_PX_PER_MM = 96 / 25.4
A4_WIDTH_MM = 210.0
_OFFSET = round(0.05 * 255)  # WCAG's 0.05, in the 8 bit scale of the luminance map

Check = Callable[[str, tuple[str, ...], int, str], Awaitable[Checked]]


SHAPES = ("sample", "lone")
UNAVAILABLE = frozenset(("renderer_unavailable", "timeout"))
# Words without a `q` or a `z`, so no filler holds a token.
_FILLER = (
    "reliable", "platform", "services", "operations", "release", "pipeline", "observable",
    "runbook", "incident", "teams", "deployment", "engineering",
)  # fmt: skip
_TOKENED = 6  # a token and its space


class CheckUnavailableError(Exception):
    """The renderer could not be reached, was busy or timed out: nothing is known of the
    template."""


@dataclass(frozen=True)
class Canary:
    kind: str
    content: c.CvContent | c.LetterContent
    view: profile_view.ProfileView
    rendered_on: date
    needles: tuple[tuple[str, str], ...]  # (field path, the text the page must show)


def _token(n: int) -> str:
    return "Q" + "".join(chr(ord("a") + (n // 26**k) % 26) for k in (2, 1, 0)) + "Z"


class _Tokens:
    def __init__(self) -> None:
        self.count = 0
        self.needles: list[tuple[str, str]] = []

    def mark(self, path: str, text: str, shown: bool = True) -> str:
        """`text` led by a token of its own; a needle when the page of this kind shows it."""
        if not text:
            return text
        marked = f"{_token(self.count)} {text}"
        self.count += 1
        if shown:
            self.needles.append((path, marked))
        return marked

    def keep(self, path: str, text: str) -> None:
        if text and all(text != found for _, found in self.needles):
            self.needles.append((path, text))


def _sample() -> dict[str, Any]:
    return json.loads((templates.BASE / "sample.json").read_text(encoding="utf-8"))


def filler(length: int) -> str:
    """Words up to `length` characters, the token that leads them left out."""
    words: list[str] = []
    size = -1
    for n in range(length):
        word = _FILLER[n % len(_FILLER)]
        if size + 1 + len(word) > length - _TOKENED:
            break
        words.append(word)
        size += 1 + len(word)
    return " ".join(words)


def _lone(data: dict[str, Any]) -> None:
    """`data` cut to one item per list, each text at its hard limit (module docstring)."""
    profile = data["profile"]
    identity = profile["identity"]
    identity["languages"] = identity["languages"][:1]
    identity["languages"][0].update(name=filler(c.LINE_MAX), level=filler(c.LINE_MAX))
    for key in ("employers", "education"):
        profile["experience"][key] = profile["experience"][key][:1]
    employer = profile["experience"]["employers"][0]
    school = profile["experience"]["education"][0]
    employer.update({key: filler(c.LINE_MAX) for key in ("organisation", "title", "location")})
    school.update({key: filler(c.LINE_MAX) for key in ("school", "degree", "location")})
    cv, letter = data["cv"], data["letter"]
    cv.update(
        headline=filler(c.HEADLINE_MAX),
        summary=filler(c.SUMMARY_MAX),
        highlights=[filler(c.BULLET_MAX)],
        competencies=[filler(c.TERM_MAX)],
        skills=[{"label": filler(c.TERM_MAX), "items": [filler(c.TERM_MAX)]}],
        experience=[{**cv["experience"][0], "bullets": [filler(c.BULLET_MAX)]}],
    )
    letter.update({key: filler(c.LINE_MAX) for key in ("recipient", "subject", "salutation")})
    letter.update(
        headline=filler(c.HEADLINE_MAX),
        closing=filler(c.LINE_MAX),
        paragraphs=[filler(c.PARAGRAPH_MAX)],
    )


@cache
def canary(kind: str, shape: str = "sample") -> Canary:
    """The canary page of `kind` in a content shape of SHAPES (module docstring)."""
    data = _sample()
    if shape == "lone":
        _lone(data)
    t = _Tokens()
    profile = data["profile"]
    identity = profile["identity"]
    cv = kind == "cv"
    # Two tokens and nothing else: a template may print each word of the name on its own.
    first, last = _token(t.count), _token(t.count + 1)
    t.count += 2
    identity["full_name"] = f"{first} {last}"
    t.needles += [("name", first), ("name", last)]
    # A contact line holds no token: its city is one no other field contains.
    identity["city"] = "Villeurbanne"
    for n, item in enumerate(identity.get("languages", [])):
        item["name"] = t.mark(f"languages[{n}].name", item["name"], cv)
        item["level"] = t.mark(f"languages[{n}].level", item.get("level", ""), cv)
    for n, employer in enumerate(profile["experience"]["employers"]):
        for key in ("organisation", "title", "location"):
            employer[key] = t.mark(f"roles[{n}].{key}", employer.get(key, ""), cv)
    for n, school in enumerate(profile["experience"]["education"]):
        school.setdefault("location", "Grenoble, France")
        for key in ("school", "degree", "location"):
            school[key] = t.mark(f"schools[{n}].{key}", school.get(key, ""), cv)
    view = profile_view.build(profile, revision=1)
    for line in templates.contact_lines(view):
        t.keep("contact[].text", str(line["text"]))
    rendered_on = date.fromisoformat(data["rendered_on"])
    raw = data[kind]
    for key in ("headline", "summary", "recipient", "subject", "salutation", "closing"):
        if key in raw:
            raw[key] = t.mark(f"content.{key}", raw[key])
    for key in ("highlights", "competencies", "paragraphs"):
        if key in raw:
            raw[key] = [t.mark(f"content.{key}[{n}]", v) for n, v in enumerate(raw[key])]
    for n, group in enumerate(raw.get("skills", [])):
        group["label"] = t.mark(f"content.skills[{n}].label", group["label"])
        group["items"] = [
            t.mark(f"content.skills[{n}].items[{m}]", v) for m, v in enumerate(group["items"])
        ]
    for n, role in enumerate(raw.get("experience", [])):
        role["bullets"] = [
            t.mark(f"roles[{n}].bullets[{m}]", v) for m, v in enumerate(role["bullets"])
        ]
    if cv:
        for n, employer in enumerate(view.employers):
            t.keep(
                f"roles[{n}].period", templates.format_period(employer.start, employer.end, "en")
            )
        for n, school in enumerate(view.schools):
            period = templates.format_period(school.start, school.end, "en", ongoing=False)
            t.keep(f"schools[{n}].period", period)
    else:
        t.keep("date", templates.format_date(rendered_on, "en"))
    content = c.MODELS[kind].model_validate(raw)
    return Canary(kind, content, view, rendered_on, tuple(t.needles))


@cache
def grey_photo() -> str:
    image = Image.new("RGB", (300, 300), (128, 128, 128))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return photos.embed(out.getvalue())


def text_width(step: templates.Step) -> int:
    """The width of an A4 page's text at the step's margin, in CSS pixels: the page box print
    lays the root element out in, so what reaches into the margin shows."""
    return round((A4_WIDTH_MM - 2 * step.margin) * CSS_PX_PER_MM)


def _linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


_LUMINANCE = [round(255 * _linear(v / 255)) for v in range(256)]
_TRIPLE = [min(255, 3 * v + 2 * _OFFSET) for v in range(256)]


def strong_pixels(before: Image.Image, after: Image.Image) -> Image.Image:
    """Per pixel, how far the lighter of the two pixels passes CONTRAST times the darker, in the
    8 bit luminance scale: above 0 where the text, painted or not, differs by 3:1 or more."""
    a = before.convert("L").point(_LUMINANCE)
    b = after.convert("L").point(_LUMINANCE)
    high, low = ImageChops.lighter(a, b), ImageChops.darker(a, b)
    return ImageChops.subtract(high, low.point(_TRIPLE))


def _open(png: bytes, size: tuple[int, int]) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    if image.size != size or image.format != "PNG":
        raise RenderError("render_failed")
    return image


def problems_of(
    checked: Checked, needles: tuple[tuple[str, str], ...], file: str, where: str
) -> list[Problem]:
    """The fields of one check that the page does not truly show (module docstring)."""
    size = (checked.width * checked.scale, checked.shown * checked.scale)
    strong = strong_pixels(_open(checked.before, size), _open(checked.after, size))
    found: dict[tuple[str, str], None] = {}
    for (path, _), seen in zip(needles, checked.found, strict=True):
        codes: list[str] = [] if seen else ["unprinted"]
        for occurrence in seen:
            if occurrence.size < MIN_SIZE_PX:
                codes.append("small")
            for x, y, w, h, own in occurrence.glyphs:
                codes.append(_glyph(checked, strong, x, y, w, h, own))
        for code in codes:
            if code:
                found[(path, code)] = None
    return [
        Problem("style.css" if code != "unprinted" else file, 0, code, f"{path} at {where}")
        for path, code in found
    ]


def _glyph(
    checked: Checked, strong: Image.Image, x: float, y: float, w: float, h: float, own: bool
) -> str:
    if w <= 0 or h <= 0:
        return "hidden"
    if x < -EDGE_PX or y < -EDGE_PX or x + w > checked.width + EDGE_PX:
        return "outside"
    if y + h > checked.shown + EDGE_PX:
        return "outside"
    if not own:
        return "covered"
    s = checked.scale
    left, top = int(x * s), int(y * s)
    right, bottom = max(left + 1, math.ceil((x + w) * s)), max(top + 1, math.ceil((y + h) * s))
    counts = strong.crop((left, top, right, bottom)).histogram()
    if sum(counts[1:]) < MIN_PIXELS:
        return "contrast"
    return ""


async def _one(
    template: templates.Template,
    kind: str,
    shape: str,
    step: templates.Step,
    label: str,
    check: Check,
) -> tuple[list[Problem], int]:
    sample = canary(kind, shape)
    photo = grey_photo() if template.photo != "none" else None
    html = templates.build_html(
        template, kind, sample.content, sample.view, "en", step, sample.rendered_on, photo=photo
    )
    texts = tuple(text for _, text in sample.needles)
    file = f"{kind}.html.j2"
    where = f"the {label} step" + (", one item per list and long texts" if shape == "lone" else "")
    try:
        checked = await check(html, texts, text_width(step), "jc" + secrets.token_hex(8))
    except RenderError as error:
        if error.code in UNAVAILABLE:
            raise CheckUnavailableError from None
        return [Problem(file, 0, "render", f"{where}: it does not lay out")], 0
    try:
        found = await asyncio.to_thread(problems_of, checked, sample.needles, file, where)
    except (RenderError, OSError, Image.DecompressionBombError):
        return [Problem(file, 0, "render", f"{where}: it does not lay out")], 0
    return found, checked.height


async def check_template(template: templates.Template, check: Check | None = None) -> list[Problem]:
    """Every problem of the render check of a template, in a fixed order; [] accepts it. Raises
    CheckUnavailableError when the renderer cannot be reached."""
    check = check or render_client.check
    problems: list[Problem] = []
    for kind in templates.KINDS:
        steps = template.steps(kind)
        heights: dict[str, int] = {}
        for shape in SHAPES:
            for step, label in ((steps[0], "loosest"), (steps[-1], "tightest")):
                found, height = await _one(template, kind, shape, step, label, check)
                problems += found
                if shape == "sample":
                    heights[label] = height
        loose_height, tight_height = heights["loosest"], heights["tightest"]
        if loose_height and tight_height and tight_height >= loose_height:
            detail = f"{kind}: the tightest step is not shorter than the loosest"
            problems.append(Problem("style.css", 0, "density_unread", detail))
    return list(dict.fromkeys(problems))[:PROBLEMS_MAX]
