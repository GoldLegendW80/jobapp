"""The fonts of an uploaded PDF CV, chosen by measurement (task D28; SPEC-documents.md, "Design
capture of an upload", step 2). Pure: Pillow only, no file but the bundled fonts is read, and
nothing is logged: the word samples are the person's text and the pixels of their CV.

    faces(design) -> one font_map.Mapped per font of design.fonts

For each font of the file, in this order:
- `exact`: its name, without the subset prefix and the style words, is a family of the library
  (font_map.asked): that family, at the face CSS matching picks for the asked weight and italic;
- `measured`: the file has SAMPLES_MIN word samples or more of it (design_pdf's `samples`):
  every family of font_map.FAMILIES, at its nearest face to the asked weight and italic, is
  scored on them, and the best score wins (`score`):
  * the width: per word, the log of the face's advances at the word's size (Pillow's basic
    layout, character by character, so kerning is left out on both sides) over the measured
    advances; the median and the spread (median absolute deviation) over the words;
  * the shape: per word, its ink in the crop (pixels off the crop's median grey by INK) cut to
    its box, against the word drawn with the face, cut to its ink and scaled to the same box:
    intersection over union, the mean over the words;
  * shape - WIDTH * |median| - SPREAD * spread: the shape first, a constant width ratio costs
    less than an uneven one (D27's method: Raleway ranked first on the Raleway CV, Inter Tight
    for TT Commons Pro, Gantari for TT Hazelnuts);
- `name`: otherwise (a DOCX, a scan, a font of too few words), font_map.face by name as before.
A symbol font is mapped by name and stays a symbol. FONTS_MEASURED fonts at most are measured
(the ones with most characters); a sample whose crop cannot be read is left out alone.

Declared open points: a family drawn with a weight the library lacks (a Light) is measured at its
nearest face, which widens and darkens it; a crop holds what lies under the word too (a rule, a
fill's edge), which lowers every family's shape alike; the basic layout draws no ligature.
"""

from __future__ import annotations

import io
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from statistics import mean, median

from PIL import Image, ImageChops, ImageDraw, ImageFont

from app.documents import font_map, templates
from app.ingestion import parser_client
from app.ingestion.parser_client import DesignPdf, WordSample

CROP_LONG_SIDE = 4 * 1568  # design_pdf.SAMPLE_SCALE times pdf_render.LONG_SIDE: pixels a page
INK = 64  # grey levels off the crop's median: a pixel of ink
SAMPLES_MIN = 3
FONTS_MEASURED = 6
WIDTH, SPREAD = 2.0, 4.0
# The pixels compared, whatever the sandbox (which the file may have taken over) sends: a crop
# over parser_client.DESIGN_SAMPLE_AREA is left out unread, each sample's ink is scaled to at
# most INK_WIDTH by INK_HEIGHT, and its face drawn at that height: at most DRAWN_MAX pixels a
# size and DRAWN_WIDTH of advance, whatever size or text the sample claims (B5 of #757).
CROP_AREA = parser_client.DESIGN_SAMPLE_AREA
INK_HEIGHT, INK_WIDTH = 96, 1_200
DRAWN_MAX = 2 * INK_HEIGHT  # pixels: the largest size a word is drawn at
DRAWN_WIDTH = 2 * INK_WIDTH  # pixels: the longest advance a word is drawn with


@dataclass(frozen=True)
class Score:
    family: str
    width: float  # the median of the logs of the width ratios: 0 is the same width
    spread: float
    shape: float  # the mean intersection over union, 0..1

    @property
    def total(self) -> float:
        return self.shape - WIDTH * abs(self.width) - SPREAD * self.spread


@dataclass(frozen=True)
class _Word:
    text: str
    size: float  # points
    width: float  # points
    ink: Image.Image  # mode L, 255 where ink, cut to it
    scale: float  # pixels per point of the crop


def _ink(image: Image.Image, threshold: int = INK) -> Image.Image | None:
    """The pixels of `image` (mode L) off its median grey, cut to their box; None when none."""
    histogram = image.histogram()
    half, seen, back = image.width * image.height / 2, 0, 255
    for level, count in enumerate(histogram):
        seen += count
        if seen >= half:
            back = level
            break
    lut = [255 if abs(level - back) > threshold else 0 for level in range(256)]
    mask = image.point(lut)
    box = mask.getbbox()
    return mask.crop(box) if box else None


def _iou(first: Image.Image, second: Image.Image) -> float:
    second = second.resize(first.size, Image.Resampling.BILINEAR).point(
        [255 if level > 127 else 0 for level in range(256)]
    )
    both = ImageChops.multiply(first, second).histogram()[255]
    either = ImageChops.lighter(first, second).histogram()[255]
    return both / either if either else 0.0


@cache
def _font(path: str, pixels: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, pixels, layout_engine=ImageFont.Layout.BASIC)


@cache
def _face_path(family: str, weight: int, italic: bool) -> str | None:

    style = "italic" if italic else "normal"
    found = templates.fonts().get(family)
    if found is None:
        return None
    # the latin subset, which holds the letters of a CV of a Latin script: never latin-ext, whose
    # file holds no basic letter (each would be drawn as the same empty box)
    name = f"latin-{weight}-{style}.woff2"
    for face in found.faces:
        if face.weight == weight and face.style == style and face.path.name == name:
            return str(face.path)
    return None


def _advance(path: str, text: str, size: float) -> float:
    """The advances of `text` at `size` points, character by character."""
    font = _font(path, 100)
    return sum(font.getlength(ch) for ch in text) * size / 100


def _drawn(path: str, word: _Word) -> Image.Image | None:
    pixels = max(4, min(round(word.size * word.scale), DRAWN_MAX))
    font = _font(path, pixels)
    length = font.getlength(word.text)
    if length > DRAWN_WIDTH:
        pixels = max(4, int(pixels * DRAWN_WIDTH / length))
        font = _font(path, pixels)
    canvas = Image.new("L", (round(font.getlength(word.text)) + 2 * pixels, 3 * pixels), 0)
    ImageDraw.Draw(canvas).text((pixels, pixels), word.text, font=font, fill=255)
    return _ink(canvas, 127)


def score(family: str, weight: int, italic: bool, words: list[_Word]) -> Score | None:
    """The width and the shape of `family`'s nearest face on the words, or None (no file)."""
    face_weight, face_italic = font_map.nearest_face(family, weight, italic)
    path = _face_path(family, face_weight, face_italic)
    if path is None:
        return None
    logs, shapes = [], []
    for word in words:
        advance = _advance(path, word.text, word.size)
        if advance > 0:
            logs.append(math.log(advance / word.width))
        drawn = _drawn(path, word)
        if drawn is not None:
            shapes.append(_iou(word.ink, drawn))
    if not logs:
        return None
    middle = median(logs)
    return Score(family, middle, median(abs(v - middle) for v in logs), mean(shapes or [0.0]))


def _bounded(ink: Image.Image, scale: float) -> tuple[Image.Image, float]:
    """The ink at most INK_WIDTH by INK_HEIGHT, and the scale it is then drawn at."""
    shrink = min(1.0, INK_HEIGHT / ink.height, INK_WIDTH / ink.width)
    if shrink >= 1.0:
        return ink, scale
    size = (max(1, round(ink.width * shrink)), max(1, round(ink.height * shrink)))
    small = ink.resize(size, Image.Resampling.BOX)
    return small.point([255 if level > 127 else 0 for level in range(256)]), scale * shrink


def words_of(samples: Iterable[WordSample], scales: Mapping[int, float]) -> list[_Word]:
    """The samples whose crop reads and holds ink, at the scale of their page, each bounded
    (`_bounded`): a crop over CROP_AREA is never decoded."""
    out = []
    for sample in samples:
        if sample.page not in scales or sample.size <= 0 or sample.width <= 0:
            continue
        try:
            with Image.open(io.BytesIO(sample.crop)) as image:
                if image.format != "PNG" or image.width * image.height > CROP_AREA:
                    continue
                grey = image.convert("L")
        except (OSError, ValueError, Image.DecompressionBombError):
            continue
        ink = _ink(grey)
        if ink is not None:
            ink, scale = _bounded(ink, scales[sample.page])
            out.append(_Word(sample.text, sample.size / 10, sample.width / 10, ink, scale))
    return out


def rank(words: list[_Word], weight: int, italic: bool) -> list[Score]:
    """Every family of the library scored on the words, the best first."""
    found = [score(family, weight, italic, words) for family in sorted(font_map.FAMILIES)]
    return sorted((s for s in found if s is not None), key=lambda s: (-s.total, s.family))


def faces(design: DesignPdf) -> list[font_map.Mapped]:
    """The bundled face of each font of the file (see the module docstring)."""
    scales = {
        page.index: CROP_LONG_SIDE / max(page.width / 10, page.height / 10) for page in design.read
    }
    characters = [0] * len(design.fonts)
    for page in design.read:
        for run in page.runs:
            if run.font < len(characters):
                characters[run.font] += len(run.text)
    measured = set(sorted(range(len(design.fonts)), key=lambda i: -characters[i])[:FONTS_MEASURED])
    out = []
    for index, font in enumerate(design.fonts):
        named = font_map.face(font.name, font.weight, font.italic, font.serif, font.monospace)
        weight, italic, exact = font_map.asked(font.name, font.weight, font.italic)
        if named.symbol:
            out.append(named)
            continue
        if exact is not None:
            got = font_map.nearest_face(exact, weight, italic)
            out.append(font_map.Mapped(exact, *got, notes=(f"exact: {named.css_name}",)))
            continue
        words = words_of((s for s in design.samples if s.font == index), scales)
        if index not in measured or len(words) < SAMPLES_MIN:
            out.append(named)
            continue
        ranked = rank(words, weight, italic)
        if not ranked:
            out.append(named)
            continue
        best = ranked[0]
        got = font_map.nearest_face(best.family, weight, italic)
        name = font_map.FAMILIES[best.family].name
        note = f"measured: {name}, score {best.total:.2f} on {len(words)} words"
        out.append(font_map.Mapped(best.family, *got, notes=(note,)))
    return out
