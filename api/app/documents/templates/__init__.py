"""The template system (task D4a of the documents plan; spec, "Templates").

A template is a folder `templates/<slug>/`: `manifest.json`, `cv.html.j2`, `letter.html.j2`,
`style.css`, `thumbnail.png` and `thumbnail-letter.png`. `registry()` discovers the folders once
and validates each one; an invalid folder fails the start with a TemplateError that names it. No
list of templates is written anywhere else: a template set task adds folders and nothing more.

`build_html(template, kind, content, profile_view, language, step, rendered_on)` turns a
document's content into the HTML the renderer receives:

- Jinja2's sandboxed environment with autoescape on, for every template: the content comes from
  the person's profile and from model output, and every value of it prints as text. Only code
  marks text safe (the style); a template file cannot: `|safe`, `autoescape` and `Markup` are
  refused by the authoring rules, and the filters that print markup (`UNESCAPING`) are removed
  from the environment;
- the fixed facts (name, contact lines, employers, schools, languages) from the profile view,
  never from the content; an experience entry prints only when its employer is in the view;
- the step's density as custom properties on `:root` (`--body-size`, `--line-height`,
  `--section-gap`, `--item-gap`, `--margin`), read by the template's `style.css`;
- the fonts of the bundled library (`documents/fonts/<family>/`) and the photo as `data:`
  addresses: nothing is ever fetched. Only the faces whose characters the page uses are
  embedded. A character none of the template's families has (Greek, an arrow, Hebrew, an
  emoji) is drawn by a free fallback family of the library, embedded once under its own name
  and named in every `font-family` of the style, so the renderer never falls back to a font
  the HTML did not ask for (D3's font check). A character no font of the library has (CJK) is
  replaced by its closest printable form or dropped (`printable`, which also returns D2's
  correction records for the person): a render never fails on a character;
- the style goes in by a structured step, in place of a token drawn for each render, and the
  built page is parsed once (`inspect`) before the renderer gets it.

**The authoring rules** (`problems`), applied to a bundled folder at start and to a person's own
template (D25b) alike: no script and no event handler, no outside address (`http`, `//`, a
`url(` other than `data:`), no `@import` and no `@font-face` (fonts come from the library), no
`|safe`, only the CSS that D1 measured as laying out the same in both engines (the features it
listed as ignored are refused), a photo slot only when the manifest allows one.

`resolve(db, user_id, slug)`: a bundled template, else the person's own through the loader that
D25b registers with `register_loader`; until then an unknown slug resolves to None.

Dates: `format_period` writes "March 2021 to present", "2021 to 2023", "mars 2021 à
aujourd'hui", never with a dash; `format_date` writes the letter's date from `rendered_on`.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from datetime import date
from functools import cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from jinja2 import DictLoader, StrictUndefined
from jinja2 import TemplateError as JinjaError
from jinja2.sandbox import ImmutableSandboxedEnvironment
from markupsafe import Markup
from pydantic import BaseModel

from app.documents import content as c
from app.documents import corrections
from app.documents.profile_view import ProfileView
from app.documents.rules import SoftLimits

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "_base"
FONTS = ROOT.parent / "fonts"
DEFAULT = "classic"
KINDS = ("cv", "letter")
LANGUAGES = ("en", "fr")
PHOTO = ("required", "optional", "none")
TAGS = frozenset(
    (
        "one column",
        "two columns",
        "photo",
        "no photo",
        "serif",
        "sans",
        "ats friendly",
        "compact",
        "colour",
    )
)
FILES = ("cv.html.j2", "letter.html.j2", "style.css")
THUMBNAILS = ("thumbnail.png", "thumbnail-letter.png")
THUMBNAIL_WIDTH = 480
LICENCES = ("OFL-1.1", "Apache-2.0")
SLUG = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
ACCENT = re.compile(r"^#[0-9a-f]{6}$")
PHOTO_URI = re.compile(r"^data:image/(png|jpeg);base64,[A-Za-z0-9+/]+=*$")


class TemplateError(ValueError):
    """A template refused: its slug (or folder) and the first rule it breaks."""

    def __init__(self, slug: str, reason: str) -> None:
        super().__init__(f"template {slug}: {reason}")
        self.slug = slug
        self.reason = reason


# --- density ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One density step: sizes in points, the margin in millimetres."""

    body_size: float
    line_height: float
    section_gap: float
    item_gap: float
    margin: float

    def css(self) -> str:
        return (
            f"@page{{margin:{self.margin:g}mm}}"
            f":root{{--body-size:{self.body_size:g}pt;--line-height:{self.line_height:g};"
            f"--section-gap:{self.section_gap:g}pt;--item-gap:{self.item_gap:g}pt;"
            f"--margin:{self.margin:g}mm}}"
        )


BOUNDS = {
    "body_size": (6.0, 14.0),
    "line_height": (1.0, 2.0),
    "section_gap": (0.0, 30.0),
    "item_gap": (0.0, 20.0),
    "margin": (5.0, 30.0),
}
SOFT_LIMITS = tuple(f.name for f in fields(SoftLimits))


@dataclass(frozen=True)
class KindSpec:
    steps: tuple[Step, ...]  # loosest first
    soft_limits: SoftLimits


@dataclass(frozen=True)
class Template:
    slug: str
    version: int
    name: Mapping[str, str]
    description: Mapping[str, str]
    tags: tuple[str, ...]
    photo: str
    fonts: tuple[str, ...]  # family folders of the library, the first one the main family
    accent: str
    kinds: Mapping[str, KindSpec]
    thumbnail_of: str
    files: Mapping[str, str]  # FILES: their text
    folder: Path | None = None  # None: a person's own template (a row, D25b)

    def steps(self, kind: str) -> tuple[Step, ...]:
        return self.kinds[kind].steps


# --- loading and the authoring rules ------------------------------------------------------------

# Refused in a template file. Each pattern is a rule of the spec or a CSS feature that D1 found
# laid out differently in the two engines (or not at all), whatever the engine chosen.
_ANYWHERE = (
    (
        re.compile(
            r"<\s*/?\s*(script|i?frame|frameset|object|embed|applet|link|base|form)\b", re.I
        ),
        "a tag",
    ),
    (re.compile(r"http-equiv", re.I), "a meta refresh"),
    (re.compile(r"\son[a-z]+\s*=", re.I), "an event handler"),
    (re.compile(r"javascript\s*:", re.I), "a javascript address"),
    (re.compile(r"https?\s*:", re.I), "an outside address"),
    (re.compile(r"//"), "an outside address"),
    (re.compile(r"url\s*\(\s*(?![\"']?data:)", re.I), "a url( that is not data:"),
    (re.compile(r"@import\b", re.I), "@import"),
)
_TEMPLATE = (
    (re.compile(r"\|\s*safe\b"), "|safe"),
    (re.compile(r"\bautoescape\b"), "autoescape"),
    (re.compile(r"\bMarkup\b"), "Markup"),
)
_CSS = (
    (re.compile(r"<"), "a < in CSS"),
    (re.compile(r"@font-face\b", re.I), "@font-face (fonts come from the library)"),
    (re.compile(r"\b(min|max|clamp|calc)\s*\(", re.I), "min(), max(), clamp() or calc()"),
    (re.compile(r"aspect-ratio|inline-flex|box-shadow|\bfilter\s*:", re.I), "unmeasured CSS"),
    (re.compile(r"hyphens\s*:\s*auto", re.I), "hyphens: auto"),
)
_PHOTO_SLOT = re.compile(r"\b(photo|portrait)\b")


def problems(files: Mapping[str, str], photo: str) -> list[str]:
    """The authoring rules a template's files break, in a fixed order; [] when none."""
    found: list[str] = []
    for name in FILES:
        text = files.get(name, "")
        rules = _ANYWHERE + (_CSS if name.endswith(".css") else _TEMPLATE)
        found += [f"{name}: {what}" for pattern, what in rules if pattern.search(text)]
        if photo == "none" and name.endswith(".j2") and _PHOTO_SLOT.search(text):
            found.append(f"{name}: a photo slot in a template without photo")
    return found


def _number(value: object, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
        raise ValueError(f"a number from {low:g} to {high:g}")
    return float(value)


def _kind(slug: str, kind: str, data: object) -> KindSpec:
    if not isinstance(data, dict) or set(data) != {"steps", "soft_limits"}:
        raise TemplateError(slug, f"{kind}: steps and soft_limits, nothing else")
    raw = data["steps"]
    if not isinstance(raw, list) or not raw:
        raise TemplateError(slug, f"{kind}: at least one step")
    steps: list[Step] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != set(BOUNDS):
            raise TemplateError(slug, f"{kind}: step {index} names {', '.join(BOUNDS)}")
        try:
            values = {key: _number(item[key], *BOUNDS[key]) for key in BOUNDS}
        except ValueError as error:
            raise TemplateError(slug, f"{kind}: step {index}: {error}") from None
        steps.append(Step(**values))
    for index in range(1, len(steps)):
        looser, tighter = steps[index - 1], steps[index]
        pairs = [(getattr(looser, key), getattr(tighter, key)) for key in BOUNDS]
        if any(b > a for a, b in pairs) or all(a == b for a, b in pairs):
            raise TemplateError(slug, f"{kind}: steps not loosest first at step {index}")
    limits = data["soft_limits"]
    if not isinstance(limits, dict) or not set(limits) <= set(SOFT_LIMITS):
        raise TemplateError(slug, f"{kind}: soft_limits names only {', '.join(SOFT_LIMITS)}")
    for key, value in limits.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise TemplateError(slug, f"{kind}: soft limit {key} is a whole number or null")
    defaults = {key: None for key in SOFT_LIMITS} if kind == "letter" else {}
    return KindSpec(tuple(steps), SoftLimits(**{**defaults, **limits}))


def _languages(slug: str, key: str, value: object) -> dict[str, str]:
    if (
        not isinstance(value, dict)
        or set(value) != set(LANGUAGES)
        or not all(isinstance(text, str) and text.strip() for text in value.values())
    ):
        raise TemplateError(slug, f"{key}: a text in en and fr")
    return dict(value)


MANIFEST_KEYS = frozenset(
    (
        "slug",
        "version",
        "name",
        "description",
        "tags",
        "photo",
        "fonts",
        "accent",
        *KINDS,
        "thumbnail_of",
    )
)


def from_files(
    manifest: Mapping[str, Any], files: Mapping[str, str], folder: Path | None = None
) -> Template:
    """A template from its manifest and its three files, validated: the same rules for a folder
    of the repository and for a person's own template."""
    slug = str(manifest.get("slug") or (folder.name if folder else "?"))
    missing = sorted(MANIFEST_KEYS - set(manifest))
    if missing:
        raise TemplateError(slug, f"manifest without {', '.join(missing)}")
    extra = sorted(set(manifest) - MANIFEST_KEYS)
    if extra:
        raise TemplateError(slug, f"manifest with unknown {', '.join(extra)}")
    if not isinstance(manifest["slug"], str) or not SLUG.match(slug):
        raise TemplateError(slug, "slug: lower case words joined by hyphens")
    if folder is not None and folder.name != slug:
        raise TemplateError(slug, f"slug differs from its folder {folder.name}")
    version = manifest["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise TemplateError(slug, "version: a whole number from 1")
    tags = manifest["tags"]
    if not isinstance(tags, list) or not tags or not set(tags) <= TAGS:
        raise TemplateError(slug, f"tags: some of {', '.join(sorted(TAGS))}")
    if manifest["photo"] not in PHOTO:
        raise TemplateError(slug, f"photo: one of {', '.join(PHOTO)}")
    families = manifest["fonts"]
    library = fonts()
    if (
        not isinstance(families, list)
        or not families
        or not all(
            isinstance(f, str) and f in library and not library[f].fallback for f in families
        )
    ):
        raise TemplateError(slug, "fonts: families of the bundled library")
    if not isinstance(manifest["accent"], str) or not ACCENT.match(manifest["accent"]):
        raise TemplateError(slug, "accent: a colour written #rrggbb")
    if not isinstance(manifest["thumbnail_of"], str):
        raise TemplateError(slug, "thumbnail_of: the hash of the folder")
    absent = [name for name in FILES if not files.get(name)]
    if absent:
        raise TemplateError(slug, f"without {', '.join(absent)}")
    broken = problems(files, manifest["photo"])
    if broken:
        raise TemplateError(slug, broken[0])
    template = Template(
        slug=slug,
        version=version,
        name=_languages(slug, "name", manifest["name"]),
        description=_languages(slug, "description", manifest["description"]),
        tags=tuple(tags),
        photo=manifest["photo"],
        fonts=tuple(families),
        accent=manifest["accent"],
        kinds={kind: _kind(slug, kind, manifest[kind]) for kind in KINDS},
        thumbnail_of=manifest["thumbnail_of"],
        files={name: files[name] for name in FILES},
        folder=folder,
    )
    try:
        environment(template)
        for name in ("cv.html.j2", "letter.html.j2"):
            environment(template).get_template(name)
    except JinjaError as error:
        raise TemplateError(slug, f"does not compile: {type(error).__name__}") from None
    return template


def load_folder(folder: Path, thumbnails: bool = True) -> Template:
    """The template of a folder; `thumbnails` False skips the check that the two thumbnails
    exist (the thumbnails command makes them)."""
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise TemplateError(folder.name, "no readable manifest.json") from None
    if not isinstance(manifest, dict):
        raise TemplateError(folder.name, "manifest.json is not an object")
    files = {
        name: (folder / name).read_text(encoding="utf-8")
        for name in FILES
        if (folder / name).is_file()
    }
    template = from_files(manifest, files, folder)
    absent = [name for name in THUMBNAILS if not (folder / name).is_file()]
    if thumbnails and absent:
        raise TemplateError(template.slug, f"without {', '.join(absent)}")
    return template


def discover(root: Path = ROOT) -> dict[str, Template]:
    """Every template folder under `root` (names starting with `_` or `.` are not templates),
    validated; the first invalid one raises."""
    found: dict[str, Template] = {}
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and not folder.name.startswith(("_", ".")):
            template = load_folder(folder)
            found[template.slug] = template
    return found


@cache
def registry() -> Mapping[str, Template]:
    """The bundled templates, read once."""
    found = discover()
    if DEFAULT not in found:
        raise TemplateError(DEFAULT, "the default template is missing")
    return found


def thumbnail_hash(folder: Path, base: Path = BASE, library: Path = FONTS) -> str:
    """The hash of what a thumbnail is drawn from: the folder's files other than the
    thumbnails, the manifest without its `thumbnail_of`, the files of `_base/` (the macros, the
    labels, the sample) and the fonts of its families (each `family.json` and face file). What
    `thumbnail_of` must equal: a change to any of them makes the thumbnails stale."""
    digest = hashlib.sha256()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("thumbnail_of", None)
    parts = [("manifest.json", json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode())]
    parts += [(name, (folder / name).read_bytes()) for name in FILES]
    parts += [(f"_base/{p.name}", p.read_bytes()) for p in sorted(base.iterdir()) if p.is_file()]
    for family in manifest.get("fonts", []):
        parts += [
            (f"fonts/{family}/{p.name}", p.read_bytes())
            for p in sorted((library / family).iterdir())
            if p.is_file()
        ]
    for name, data in parts:
        digest.update(name.encode() + b"\0" + hashlib.sha256(data).digest() + b"\0")
    return digest.hexdigest()


# --- the person's own templates (D25b) ----------------------------------------------------------

Loader = Callable[[Any, Any, str], Template | None]
_loader: Loader | None = None


def register_loader(loader: Loader | None) -> None:
    """D25b's loader of a person's own template: `loader(db, user_id, slug)`, which returns a
    Template built by `from_files`, or None."""
    global _loader
    _loader = loader


def resolve(db: Any, user_id: Any, slug: str) -> Template | None:
    """A bundled template first, else the person's own; None when neither exists."""
    bundled = registry().get(slug)
    if bundled is not None or _loader is None:
        return bundled
    return _loader(db, user_id, slug)


# --- fonts --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Face:
    path: Path
    weight: int
    style: str
    points: frozenset[int]  # the characters it draws: its unicode_range, which is truthful
    unicode_range: str

    def covers(self, codepoint: int) -> bool:
        return codepoint in self.points


@dataclass(frozen=True)
class Family:
    id: str
    name: str
    licence: str
    fallback: bool  # drawn only for the characters the template's own families lack
    faces: tuple[Face, ...]

    def covers(self, codepoint: int) -> bool:
        return any(codepoint in face.points for face in self.faces)


# The free fallback families, in the order a character missing from a template's families looks
# for one (text before symbols, the large Math and Symbols 2 faces after the small ones).
FALLBACKS = (
    "noto-sans",
    "noto-sans-math",
    "noto-sans-symbols",
    "noto-sans-symbols-2",
    "noto-sans-hebrew",
    "noto-sans-arabic",
    "noto-emoji",
)


def _points(text: str) -> frozenset[int]:
    out: set[int] = set()
    for part in text.split(","):
        bounds = part.strip().upper().removeprefix("U+").split("-")
        out.update(range(int(bounds[0], 16), int(bounds[-1], 16) + 1))
    return frozenset(out)


@cache
def fonts() -> Mapping[str, Family]:
    """The bundled library: every `fonts/<family>/family.json`, with its licence file. A face's
    `unicode_range` is the range of its subset limited to the glyphs the file holds, so a face
    that claims a character draws it."""
    found: dict[str, Family] = {}
    for folder in sorted(FONTS.iterdir()):
        if not (folder / "family.json").is_file():
            continue
        data = json.loads((folder / "family.json").read_text(encoding="utf-8"))
        if data["licence"] not in LICENCES or not (folder / "LICENSE.txt").is_file():
            raise TemplateError(folder.name, "a font without a free licence file")
        faces = tuple(
            Face(
                folder / face["file"],
                face["weight"],
                face["style"],
                _points(face["unicode_range"]),
                face["unicode_range"],
            )
            for face in data["faces"]
        )
        found[folder.name] = Family(
            folder.name, data["family"], data["licence"], data["fallback"], faces
        )
    if [key for key, family in found.items() if family.fallback] != sorted(FALLBACKS):
        raise TemplateError("fonts", "the fallback families differ from FALLBACKS")
    return found


@cache
def _common(families: tuple[str, ...]) -> frozenset[int]:
    """The characters every family of a template draws."""
    library = fonts()
    sets = [frozenset().union(*(f.points for f in library[key].faces)) for key in families]
    return frozenset.intersection(*sets)


@cache
def _printable(families: tuple[str, ...]) -> frozenset[int]:
    """The characters a template can print: those of all its families, and those of a fallback."""
    library = fonts()
    return _common(families).union(*(f.points for k in FALLBACKS for f in library[k].faces))


# Characters that need no glyph: controls (a new line), format characters (a joiner, a mark of
# direction), separators of lines and paragraphs.
_NO_GLYPH = frozenset(("Cc", "Cf", "Zl", "Zp"))


def printable_text(families: tuple[str, ...], text: str, tidy: bool = True) -> str:
    """`text` with each character no font of the template draws (its own families and the
    fallbacks) replaced by its compatibility form when that one prints (a full width
    letter -> its plain letter, an ideographic space -> a space), else
    removed; with `tidy`, runs of spaces left behind become one."""
    if text.isascii():
        return text
    keep = _printable(families)
    out: list[str] = []
    for ch in text:
        if ord(ch) in keep or unicodedata.category(ch) in _NO_GLYPH:
            out.append(ch)
            continue
        near = unicodedata.normalize("NFKC", ch)
        if near != ch and all(ord(x) in keep for x in near):
            out.append(near)
    fixed = "".join(out)
    return fixed if fixed == text or not tidy else re.sub(r" {2,}", " ", fixed).strip()


def printable(
    template: Template, kind: str, content: BaseModel | Mapping[str, Any]
) -> tuple[c.CvContent | c.LetterContent, list[corrections.Correction]]:
    """The content with what the template cannot print replaced (`printable_text`), and one
    correction record (`not_printable`, D2's record) per field changed, for the person to see.
    The pipeline stores these records with the version; `build_html` applies the same
    replacement itself, so a render never fails on a character (spec: never block)."""
    parsed = content if isinstance(content, BaseModel) else c.MODELS[kind].model_validate(content)
    return corrections.replace_unprintable(
        kind,  # type: ignore[arg-type]
        parsed,  # type: ignore[arg-type]
        lambda text: printable_text(template.fonts, text),
    )


@cache
def _data_uri(path: Path) -> str:
    return "data:font/woff2;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _face_rule(name: str, face: Face, weight: str = "") -> str:
    return (
        f'@font-face{{font-family:"{name}";src:url({_data_uri(face.path)}) format("woff2");'
        f"font-weight:{weight or face.weight};font-style:{face.style};"
        f"unicode-range:{face.unicode_range}}}"
    )


def font_css(families: tuple[str, ...], text: str) -> tuple[str, tuple[str, ...]]:
    """The `@font-face` rules for the characters of `text`, and the fallback families they use.

    A face of the template's families is embedded when it draws one of the characters. A
    character that one of those families lacks is drawn by the first fallback family (in
    FALLBACKS order) that has it: its faces holding that character are embedded once, under the
    fallback's own name, whatever the number of families, weights and styles of the template;
    `with_fallbacks` names them in every `font-family` of the style, before the generic family
    (the font check of D3 accepts only fonts the HTML names). A character no family has is not
    in the text (`printable_text`)."""
    library = fonts()
    used = {ord(ch) for ch in text if ord(ch) >= 0x20}
    rules = [
        _face_rule(library[key].name, face)
        for key in families
        for face in library[key].faces
        if not face.points.isdisjoint(used)
    ]
    chosen: dict[str, set[Face]] = {}
    for codepoint in sorted(used - _common(families)):
        for key in FALLBACKS:
            faces = {face for face in library[key].faces if codepoint in face.points}
            if faces:
                chosen.setdefault(key, set()).update(faces)
                break
    names: list[str] = []
    for key in FALLBACKS:
        if key in chosen:
            family = library[key]
            names.append(family.name)
            # A family of one weight serves every weight as it is: a bold the renderer made up
            # would print as a font without a name (a Type 3 font), which D3's check refuses.
            weight = "100 900" if len({f.weight for f in family.faces}) == 1 else ""
            rules += [_face_rule(family.name, f, weight) for f in family.faces if f in chosen[key]]
    return "".join(rules), tuple(names)


GENERIC = frozenset(
    (
        "serif",
        "sans-serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "ui-serif",
        "ui-sans-serif",
        "ui-monospace",
        "ui-rounded",
        "emoji",
        "math",
        "fangsong",
    )
)
_WIDE = frozenset(("inherit", "initial", "unset", "revert", "revert-layer"))
_FONT_FAMILY = re.compile(r"(?<![\w-])(font-family\s*:)([^;}]*)", re.I)


def with_fallbacks(css: str, names: tuple[str, ...]) -> str:
    """`css` with the fallback families `names` in every `font-family` list, before its first
    generic family (a generic name resolves to a system font, which would draw the character
    first), else at its end."""
    if not names:
        return css
    added = [f'"{name}"' for name in names]

    def one(match: re.Match[str]) -> str:
        value, important = match.group(2), ""
        if "!" in value:
            value, _, rest = value.partition("!")
            important = "!" + rest
        items = [item.strip() for item in value.split(",")]
        if len(items) == 1 and items[0].lower() in _WIDE:
            return match.group(0)
        at = next((i for i, item in enumerate(items) if item.lower() in GENERIC), len(items))
        return match.group(1) + ",".join(items[:at] + added + items[at:]) + important

    return _FONT_FAMILY.sub(one, css)


# --- dates and labels ---------------------------------------------------------------------------


@cache
def labels() -> Mapping[str, Mapping[str, Any]]:
    return json.loads((BASE / "labels.json").read_text(encoding="utf-8"))


def _point(value: str, language: str) -> str:
    """'2021-03' -> 'March 2021' or 'mars 2021'; '2021' stays '2021'."""
    year, _, month = value.partition("-")
    if not month:
        return year
    return f"{labels()[language]['months'][int(month) - 1]} {year}"


def format_period(start: str | None, end: str | None, language: str, ongoing: bool = True) -> str:
    """'March 2021 to present', '2021 to 2023', 'mars 2021 à aujourd'hui', '2021' (a year
    alone), '' (no dates). With `ongoing` False (a school), a missing end prints the start alone."""
    words = labels()[language]
    first = _point(start, language) if start else ""
    last = _point(end, language) if end else ""
    if not first:
        return last
    if not last:
        return f"{first} {words['to']} {words['present']}" if ongoing else first
    if first == last:
        return first
    return f"{first} {words['to']} {last}"


def format_date(day: date, language: str) -> str:
    """The letter's date: '14 October 2026', '14 octobre 2026', '1er octobre 2026'."""
    month = labels()[language]["months"][day.month - 1]
    number = "1er" if language == "fr" and day.day == 1 else str(day.day)
    return f"{number} {month} {day.year}"


# --- contact lines ------------------------------------------------------------------------------


def _https(url: str) -> str | None:
    """The address itself when it is a plain https: address, else None (it prints as text)."""
    if not url or any(
        ch.isspace() or ch in "<>\"'`\\" or unicodedata.category(ch)[0] == "C" for ch in url
    ):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname:
        return None
    return url


def _shown(url: str) -> str:
    """An address as printed: without its scheme, `www.` and a final slash."""
    text = re.sub(r"^https?://", "", url, flags=re.I)
    return text.removeprefix("www.").rstrip("/")


def contact_lines(view: ProfileView) -> list[dict[str, str | None]]:
    """The contact lines of the profile, in a fixed order: e-mail, phone, place, then links.
    A link is an `https:` or `mailto:` address built from the profile only; anything else
    prints as plain text."""
    identity = view.identity
    lines: list[dict[str, str | None]] = []
    if identity.email:
        lines.append({"text": identity.email, "href": "mailto:" + quote(identity.email, "@.+_-")})
    if identity.phone:
        lines.append({"text": identity.phone, "href": None})
    place = ", ".join(part for part in (identity.city, identity.country) if part)
    if place:
        lines.append({"text": place, "href": None})
    seen: set[str] = set()
    for url in (identity.linkedin_url, identity.website, *(url for _, url in identity.links)):
        if url and url not in seen:
            seen.add(url)
            lines.append({"text": _shown(url), "href": _https(url)})
    return lines


# --- building the HTML --------------------------------------------------------------------------


@cache
def _base_files() -> Mapping[str, str]:
    return {
        f"_base/{path.name}": path.read_text(encoding="utf-8") for path in sorted(BASE.glob("*.j2"))
    }


def environment(template: Template) -> ImmutableSandboxedEnvironment:
    """The sandboxed environment of a template, autoescape on, undefined names an error."""
    cached = _environments.get(id(template))
    if cached is not None and cached[0] is template:
        return cached[1]
    env = ImmutableSandboxedEnvironment(
        loader=DictLoader({**_base_files(), **template.files}),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    for name in UNESCAPING:
        env.filters.pop(name, None)
    _environments[id(template)] = (template, env)
    return env


_environments: dict[int, tuple[Template, ImmutableSandboxedEnvironment]] = {}
# Jinja's filters that print markup unescaped, removed from every environment: `safe` (also
# through `map('safe')`), `tojson` (Markup that leaves `"` alone inside an attribute), `urlize`
# (links built from the text), `xmlattr` (attributes built from a dict, `onclick` included).
UNESCAPING = ("safe", "tojson", "urlize", "xmlattr")


def _content(
    template: Template, kind: str, content: BaseModel | Mapping[str, Any]
) -> dict[str, Any]:
    model = c.MODELS[kind]
    parsed = content if isinstance(content, model) else model.model_validate(content)
    return printable(template, kind, parsed)[0].model_dump(mode="json")


def build_html(
    template: Template,
    kind: str,
    content: BaseModel | Mapping[str, Any],
    profile_view: ProfileView,
    language: str,
    step: Step | int,
    rendered_on: date,
    *,
    photo: str | None = None,
) -> str:
    """The HTML of one document at one density step (see the module docstring). `photo` is a
    `data:` address made by `photo.embed`, printed only when the template has a photo slot."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind}")
    if language not in LANGUAGES:
        raise ValueError(f"unknown language {language}")
    if isinstance(step, int):
        step = template.steps(kind)[step]
    if photo is not None and not PHOTO_URI.match(photo):
        raise ValueError("the photo is not a data: address of photo.embed")
    data = _content(template, kind, content)
    by_key = {employer.key: employer for employer in profile_view.employers}
    roles = [
        {
            "organisation": by_key[role["employer_key"]].organisation,
            "title": by_key[role["employer_key"]].title,
            "location": by_key[role["employer_key"]].location,
            "period": format_period(
                by_key[role["employer_key"]].start, by_key[role["employer_key"]].end, language
            ),
            "bullets": role["bullets"],
        }
        for role in data.get("experience", [])
        if role["employer_key"] in by_key
    ]
    # The style goes in after the render, in place of a token drawn for this render that no
    # text can hold (content and profile print escaped, and cannot guess it).
    token = f"/*{secrets.token_hex(16)}*/"
    style = Markup(token)  # noqa: S704
    context = {
        "lang": language,
        "labels": labels()[language],
        "kind": kind,
        "content": data,
        "name": profile_view.identity.full_name,
        "contact": contact_lines(profile_view),
        "roles": roles,
        "schools": [
            {
                "school": school.school,
                "degree": school.degree,
                "location": school.location,
                "period": format_period(school.start, school.end, language, ongoing=False),
            }
            for school in profile_view.schools
        ],
        "languages": [{"name": lang.name, "level": lang.level} for lang in profile_view.languages],
        "photo": photo if template.photo != "none" else None,
        "date": format_date(rendered_on, language),
        "accent": template.accent,
        "style": style,
    }
    page = environment(template).get_template(f"{kind}.html.j2").render(context)
    if page.count(token) != 1:
        raise TemplateError(template.slug, "the style is not printed exactly once")
    # The profile's own text (a name, a school) through the same replacement as the content.
    page = printable_text(template.fonts, page, tidy=False)
    css = step.css() + f":root{{--accent:{template.accent}}}" + template.files["style.css"]
    faces, names = font_css(template.fonts, html.unescape(page.replace(token, "")) + css)
    page = page.replace(token, faces + with_fallbacks(css, names), 1)
    inspect(template.slug, page)
    return page


# --- the check of the built HTML ----------------------------------------------------------------

_REFUSED_TAGS = frozenset(
    ("script", "iframe", "frame", "frameset", "object", "embed", "applet", "link", "base", "form")
)


class _Inspector(HTMLParser):
    """The first breach of the authoring rules in a built page, if any."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.breach: str | None = None
        self._in_head = False

    def _refuse(self, what: str) -> None:
        self.breach = self.breach or what

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "head":
            self._in_head = True
        elif tag == "body":
            self._in_head = False
        if tag in _REFUSED_TAGS:
            self._refuse(f"a <{tag}>")
        if tag == "style" and not self._in_head:
            self._refuse("a <style> outside the head")
        for key, value in attrs:
            address = "".join((value or "").split()).lower()
            if key.startswith("on"):
                self._refuse(f"an event handler {key}")
            elif key == "http-equiv":
                self._refuse("a meta refresh")
            elif "javascript:" in address or "vbscript:" in address:
                self._refuse("a javascript address")
            elif key == "href" and not address.startswith(("https:", "mailto:")):
                self._refuse("a link other than https: or mailto:")
            elif key == "src" and not address.startswith("data:image/"):
                self._refuse("a source other than a data: image")

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._in_head = False


def inspect(slug: str, page: str) -> None:
    """Parses a built page once, before the renderer gets it: no script, no event handler, no
    `javascript:` address, no link but https: or mailto:, no source but a data: image, no
    <style> outside the head. The authoring rules hold at load; this holds whatever a template
    (a person's own, D25b) found to print, and a breach raises a TemplateError."""
    inspector = _Inspector()
    inspector.feed(page)
    inspector.close()
    if inspector.breach:
        raise TemplateError(slug, f"the built page holds {inspector.breach}")
