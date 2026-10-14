"""The template system (task D4a), table driven over EVERY folder of `templates/`: a template set
task adds folders and nothing else, and these tests are what accepts them.

Per template: the manifest and the thumbnails, `thumbnail_of` up to date, the authoring rules on
its files and on the HTML it builds, every field escaped (hostile strings in every field of the
content and of the profile), both languages. Marked `docker`: the spec's fit rule on D1's and
D2's fixtures through the real renderer (the stack of test_renderer_container.py, its image
under a tag of its own unless RENDERER_TEST_IMAGE names one) and the fonts the PDF embeds.

Then the system itself: the registry's refusals (each names the template), the dates, the
contact lines, the photo, the fixed facts, the font library and its fallbacks, `resolve`.
"""

import asyncio
import base64
import io
import json
import re
import shutil
from datetime import date
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from jinja2.exceptions import SecurityError
from PIL import Image

from app.documents import content as c
from app.documents import fit, photo, profile_view, render_client, templates
from app.documents.profile_view import Employer, Identity, Language, ProfileView, School
from app.documents.render_client import Rendered
from app.documents.templates import TemplateError, thumbnails
from tests.documents.conftest import FIXTURES, base_cv, view_of, written
from tests.documents.test_renderer_container import stack as renderer_stack

FOLDERS = sorted(
    path
    for path in templates.ROOT.iterdir()
    if path.is_dir() and not path.name.startswith(("_", "."))
)
SLUGS = [path.name for path in FOLDERS]
ON = date(2026, 10, 14)
CONTENT = FIXTURES / "content"
D1_CVS = ("cv-short", "cv-typical", "cv-long", "cv-tightest")
D1_LETTERS = ("letter-en", "letter-fr")
D2_FIXTURES = ("cv_short", "cv_typical", "cv_long", "letter_typical", "letter_fr")


def template(slug: str) -> templates.Template:
    return templates.registry()[slug]


def sample(kind: str) -> dict[str, Any]:
    return thumbnails.sample()[kind]


def d1(name: str) -> tuple[str, str, Any]:
    """A content fixture of D1. They predate D2's hard limits: `cv-tightest` has ten bullets for
    its main employer, cut here to the eight a CV can hold (still the tightest fixture)."""
    data = json.loads((CONTENT / f"{name}.json").read_text(encoding="utf-8"))
    content = data["content"]
    for role in content.get("experience", []):
        role["bullets"] = role["bullets"][: c.BULLETS_MAX]
    return data["kind"], data["language"], c.MODELS[data["kind"]].model_validate(content)


def d1_view() -> ProfileView:
    """D1's invented fixed facts (fixtures/content/profile.json) as a profile view."""
    data = json.loads((CONTENT / "profile.json").read_text(encoding="utf-8"))
    identity = data["identity"]
    city, _, country = identity["location"].partition(", ")
    sections = {
        "identity": {
            "full_name": identity["name"],
            "email": identity["email"],
            "phone": identity["phone"],
            "city": city,
            "country": country,
            "website": f"https://{identity['links'][0]}",
            "links": [
                {"label": "Code", "url": f"https://{link}"} for link in identity["links"][1:]
            ],
            "languages": data["fixed"]["languages"],
        },
        "experience": {
            "employers": data["fixed"]["employers"],
            "education": [
                {"school": s["name"], "degree": s["degree"], "start": s["start"], "end": s["end"]}
                for s in data["fixed"]["schools"]
            ],
        },
    }
    return profile_view.build(sections, revision=1)


class Page(HTMLParser):
    """What the built HTML holds: tags, attributes, text, style."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []
        self.style: list[str] = []
        self._in_style = False
        self.feed(text)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        self._in_style = tag == "style"

    def handle_endtag(self, tag: str) -> None:
        self._in_style = False

    def handle_data(self, data: str) -> None:
        (self.style if self._in_style else self.text).append(data)

    @property
    def body(self) -> str:
        return " ".join(" ".join(self.text).split())


def assert_authoring(html: str) -> Page:
    page = Page(html)
    names = [tag for tag, _ in page.tags]
    for refused in ("script", "iframe", "object", "embed", "link", "base", "form", "frame"):
        assert refused not in names, refused
    assert names.count("style") == 1
    for tag, attrs in page.tags:
        for key, value in attrs.items():
            assert not key.startswith("on"), (tag, key)
            if key == "src":
                assert (value or "").startswith("data:image/"), (tag, value)
            if key == "href":
                assert (value or "").startswith(("https://", "mailto:")), (tag, value)
            if key == "http-equiv":
                raise AssertionError("a meta refresh")
    for address in re.findall(r"url\(\s*([^)]{0,40})", "".join(page.style)):
        assert address.strip("'\" ").startswith("data:"), address
    assert "@import" not in "".join(page.style)
    return page


# --- the folders --------------------------------------------------------------------------------


def test_the_registry_holds_every_folder_and_the_default() -> None:
    assert sorted(templates.registry()) == SLUGS
    assert templates.DEFAULT in templates.registry()


@pytest.mark.parametrize("slug", SLUGS)
def test_the_manifest_is_valid(slug: str) -> None:
    folder = templates.ROOT / slug
    found = templates.load_folder(folder)
    assert found.slug == slug == json.loads((folder / "manifest.json").read_text())["slug"]
    for kind in templates.KINDS:
        steps = found.steps(kind)
        assert len(steps) >= 2, "a fit loop needs room to tighten"
        for looser, tighter in pairwise(steps):
            assert tighter.body_size <= looser.body_size
            assert tighter.margin <= looser.margin
    assert found.photo in templates.PHOTO
    assert ("photo" in found.tags) == (found.photo != "none")
    assert all(family in templates.fonts() for family in found.fonts)


@pytest.mark.parametrize("slug", SLUGS)
def test_the_thumbnails_are_present_and_up_to_date(slug: str) -> None:
    folder = templates.ROOT / slug
    for name in templates.THUMBNAILS:
        with Image.open(folder / name) as image:
            assert image.format == "PNG"
            assert image.width == templates.THUMBNAIL_WIDTH
            assert image.height > image.width  # an A4 page
    assert template(slug).thumbnail_of == templates.thumbnail_hash(folder), (
        f"{slug}: run python -m app.documents.templates.thumbnails {slug}"
    )


TEMPLATE_FILES = [
    path
    for folder in [templates.BASE, *FOLDERS]
    for path in sorted(folder.iterdir())
    if path.suffix in (".j2", ".css") or path.name == "manifest.json"
]


@pytest.mark.parametrize("path", TEMPLATE_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_template_file_names_an_outside_address(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "http" not in text.lower()
    assert "//" not in text
    for address in re.findall(r"url\(\s*([^)]{0,20})", text, re.IGNORECASE):
        assert address.strip("'\" ").startswith("data:"), address
    assert "<script" not in text.lower()
    assert "|safe" not in text.replace(" ", "")
    role = {".css": "style.css", ".j2": "cv.html.j2"}.get(path.suffix)
    if role:
        assert templates.problems({role: text}, "optional") == []


@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_the_built_html_follows_the_authoring_rules(slug: str, kind: str, language: str) -> None:
    html = templates.build_html(
        template(slug), kind, sample(kind), thumbnails.sample_view(), language, 0, ON
    )
    page = assert_authoring(html)
    assert f'<html lang="{language}">' in html
    assert "Alex Example" in page.body
    labels = templates.labels()[language]
    if kind == "cv":
        for key in ("summary", "experience", "education", "skills", "languages"):
            assert labels[key] in page.body, key
    else:
        assert templates.format_date(ON, language) in page.body
        for field in ("recipient", "subject", "salutation", "closing"):
            assert sample(kind)[field] in page.body, field
    assert template(slug).steps(kind)[0].css() in html
    assert "font-family" in "".join(page.style)


# --- escaping -----------------------------------------------------------------------------------

LONG_WORD = "Vingt" + "trois" * 30  # 155 letters, no space
HOSTILE = (
    "<script>alert(1)</script> </style><b>bold</b> a & b \"double\" 'single' "
    "javascript:alert(1) 😀 שלום مرحبا"
)
SHORT_HOSTILE = "<script>x</script></style>&\"'😀 שלום"  # within a term's 100 characters


def hostile_cv() -> dict[str, Any]:
    return {
        "schema": 1,
        "target": HOSTILE,
        "headline": HOSTILE,
        "summary": HOSTILE + " " + LONG_WORD,
        "highlights": [HOSTILE, LONG_WORD],
        "competencies": [SHORT_HOSTILE] * 3,
        "experience": [{"employer_key": "acme", "bullets": [HOSTILE, LONG_WORD]}],
        "skills": [{"label": SHORT_HOSTILE, "items": [SHORT_HOSTILE, "javascript:alert(1)"]}],
        "notes": HOSTILE,
    }


def hostile_letter() -> dict[str, Any]:
    return {
        "schema": 1,
        "target": HOSTILE,
        "headline": HOSTILE,
        "recipient": HOSTILE,
        "subject": HOSTILE,
        "salutation": HOSTILE,
        "paragraphs": [HOSTILE, LONG_WORD],
        "closing": HOSTILE,
    }


def hostile_view() -> ProfileView:
    """A view built directly, past the profile's own validation, so the template's own guards
    are what is tested."""
    return ProfileView(
        revision=1,
        employers=(Employer("acme", HOSTILE, SHORT_HOSTILE, "2021-03", None, SHORT_HOSTILE),),
        schools=(School(SHORT_HOSTILE, SHORT_HOSTILE, None, None, ""),),
        languages=(Language(SHORT_HOSTILE, SHORT_HOSTILE),),
        identity=Identity(
            full_name=SHORT_HOSTILE,
            email="e2e-alex@example.org",
            phone=SHORT_HOSTILE,
            city=SHORT_HOSTILE,
            linkedin_url="javascript:alert(1)",
            website='https://example.org/"><script>x</script>',
            links=(("x", "http://example.org/plain"), ("y", "data:text/html,<script>x</script>")),
        ),
    )


HOSTILE_PRINTED = {
    "cv": [HOSTILE, LONG_WORD, SHORT_HOSTILE],
    "letter": [HOSTILE, LONG_WORD, SHORT_HOSTILE],
}


@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_every_field_prints_as_text(slug: str, kind: str, language: str) -> None:
    content = hostile_cv() if kind == "cv" else hostile_letter()
    html = templates.build_html(template(slug), kind, content, hostile_view(), language, 0, ON)
    page = assert_authoring(html)
    lowered = html.lower()
    assert "<script" not in lowered
    assert lowered.count("</style") == 1
    assert "<b>" not in html
    assert "javascript:" not in "".join(
        value or "" for _, attrs in page.tags for value in attrs.values()
    )
    hrefs = [attrs["href"] for tag, attrs in page.tags if tag == "a"]
    assert hrefs == ["mailto:e2e-alex@example.org"], hrefs
    for value in HOSTILE_PRINTED[kind]:
        assert " ".join(value.split()) in page.body, value[:30]
    if kind == "cv":
        assert "&lt;script&gt;" in html
    # The scripts the text needs come from the library's free fallbacks, named for the font check.
    style = "".join(page.style)
    for family in ("Noto Sans Hebrew", "Noto Sans Arabic", "Noto Emoji"):
        assert f'"{family}"' in style


def test_target_and_notes_are_never_printed() -> None:
    content = sample("cv") | {"target": "TargetMarker7f3", "notes": "NotesMarker9c1"}
    html = templates.build_html(
        template("classic"), "cv", content, thumbnails.sample_view(), "en", 0, ON
    )
    assert "TargetMarker7f3" not in html
    assert "NotesMarker9c1" not in html


def test_the_sandbox_refuses_what_a_template_should_not_reach() -> None:
    files = dict(template("classic").files)
    files["cv.html.j2"] = "{{ name.__class__.__mro__ }}"
    manifest = json.loads((templates.ROOT / "classic" / "manifest.json").read_text())
    rogue = templates.from_files(manifest, files)
    with pytest.raises(SecurityError):
        templates.build_html(rogue, "cv", sample("cv"), thumbnails.sample_view(), "en", 0, ON)


# --- the registry refuses a broken folder at start ----------------------------------------------


def copy_classic(root: Path, slug: str = "broken") -> Path:
    folder = root / slug
    shutil.copytree(templates.ROOT / "classic", folder)
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["slug"] = slug
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder


def edit_manifest(folder: Path, change: Any) -> None:
    manifest = json.loads((folder / "manifest.json").read_text())
    change(manifest)
    (folder / "manifest.json").write_text(json.dumps(manifest))


def refusal(root: Path) -> str:
    with pytest.raises(TemplateError) as raised:
        templates.discover(root)
    assert raised.value.slug == "broken"
    return raised.value.reason


def test_a_copy_of_classic_is_accepted(tmp_path: Path) -> None:
    copy_classic(tmp_path, "copy")
    assert list(templates.discover(tmp_path)) == ["copy"]


@pytest.mark.parametrize(
    "field", ["slug", "version", "name", "tags", "photo", "fonts", "accent", "cv", "thumbnail_of"]
)
def test_a_manifest_with_a_missing_field_is_refused(tmp_path: Path, field: str) -> None:
    folder = copy_classic(tmp_path)
    edit_manifest(folder, lambda m: m.pop(field))
    assert field in refusal(tmp_path)


def test_steps_not_loosest_first_are_refused(tmp_path: Path) -> None:
    folder = copy_classic(tmp_path)
    edit_manifest(folder, lambda m: m["cv"]["steps"].reverse())
    assert "loosest first" in refusal(tmp_path)


def test_two_equal_steps_are_refused(tmp_path: Path) -> None:
    folder = copy_classic(tmp_path)
    edit_manifest(folder, lambda m: m["letter"]["steps"].insert(0, m["letter"]["steps"][0]))
    assert "loosest first" in refusal(tmp_path)


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda m: m.update(fonts=["avenir-next"]), "fonts"),
        (lambda m: m.update(fonts=["noto-emoji"]), "fonts"),
        (lambda m: m.update(tags=["shiny"]), "tags"),
        (lambda m: m.update(photo="sometimes"), "photo"),
        (lambda m: m.update(accent="red"), "accent"),
        (lambda m: m.update(extra=1), "unknown"),
        (lambda m: m["cv"]["steps"][0].update(body_size=40), "step 0"),
        (lambda m: m["cv"]["soft_limits"].update(words=3), "soft_limits"),
        (lambda m: m["name"].pop("fr"), "name"),
    ],
)
def test_a_wrong_manifest_value_is_refused(tmp_path: Path, change: Any, said: str) -> None:
    folder = copy_classic(tmp_path)
    edit_manifest(folder, change)
    assert said in refusal(tmp_path)


def test_a_folder_without_thumbnails_is_refused(tmp_path: Path) -> None:
    folder = copy_classic(tmp_path)
    (folder / "thumbnail-letter.png").unlink()
    assert "thumbnail-letter.png" in refusal(tmp_path)


def test_a_folder_without_a_template_file_is_refused(tmp_path: Path) -> None:
    folder = copy_classic(tmp_path)
    (folder / "letter.html.j2").unlink()
    assert "letter.html.j2" in refusal(tmp_path)


@pytest.mark.parametrize(
    ("name", "text", "said"),
    [
        ("cv.html.j2", "<script>1</script>", "a tag"),
        ("cv.html.j2", '<img src="x" onerror="1">', "an event handler"),
        ("cv.html.j2", '<a href="https://example.org">x</a>', "an outside address"),
        ("cv.html.j2", '<img src="//example.org/x.png">', "an outside address"),
        ("cv.html.j2", "{{ content.summary|safe }}", "|safe"),
        ("cv.html.j2", "{% autoescape false %}{{ name }}{% endautoescape %}", "autoescape"),
        ("cv.html.j2", '<meta http-equiv="refresh" content="0">', "a meta refresh"),
        ("style.css", "body{background:url(x.png)}", "a url( that is not data:"),
        ("style.css", "@import 'x.css';", "@import"),
        ("style.css", "@font-face{font-family:X}", "@font-face"),
        ("style.css", "p{width:calc(100% - var(--margin))}", "calc()"),
        ("style.css", "p{box-shadow:0 0 1px red}", "unmeasured CSS"),
        ("style.css", "p{hyphens:auto}", "hyphens"),
        ("style.css", "</style><p>", "a < in CSS"),
    ],
)
def test_the_authoring_rules_refuse(tmp_path: Path, name: str, text: str, said: str) -> None:
    folder = copy_classic(tmp_path)
    with (folder / name).open("a") as handle:
        handle.write(text)
    assert said in refusal(tmp_path)


def test_a_photo_slot_needs_a_photo_in_the_manifest(tmp_path: Path) -> None:
    folder = copy_classic(tmp_path)
    edit_manifest(folder, lambda m: m.update(photo="none", tags=["one column", "no photo"]))
    with (folder / "cv.html.j2").open("a") as handle:
        handle.write("{{ photo }}")
    assert "photo slot" in refusal(tmp_path)


def test_a_template_with_an_optional_photo_prints_none_without_one(tmp_path: Path) -> None:
    """A fixture manifest of the test: `photo: optional`, a profile without photo."""
    copy_classic(tmp_path, "optional-photo")
    found = templates.discover(tmp_path)["optional-photo"]
    assert found.photo == "optional"
    html = templates.build_html(found, "cv", sample("cv"), thumbnails.sample_view(), "en", 0, ON)
    assert "<img" not in html
    assert_authoring(html)


# --- dates, labels, contact lines, fixed facts --------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "language", "printed"),
    [
        ("2021-03", None, "en", "March 2021 to present"),
        ("2021-03", None, "fr", "mars 2021 à aujourd'hui"),
        ("2021", "2023", "en", "2021 to 2023"),
        ("2021", "2023", "fr", "2021 à 2023"),
        ("2020-09", "2023-01", "en", "September 2020 to January 2023"),
        ("2021", "2021", "en", "2021"),
        (None, "2019", "en", "2019"),
        (None, None, "en", ""),
    ],
)
def test_format_period(start: str | None, end: str | None, language: str, printed: str) -> None:
    assert templates.format_period(start, end, language) == printed
    assert not re.search(r"[–—]| - ", printed)


def test_a_school_with_a_start_alone_prints_the_year_alone() -> None:
    assert templates.format_period("2019", None, "en", ongoing=False) == "2019"


def test_format_date() -> None:
    assert templates.format_date(ON, "en") == "14 October 2026"
    assert templates.format_date(ON, "fr") == "14 octobre 2026"
    assert templates.format_date(date(2026, 10, 1), "fr") == "1er octobre 2026"


def test_the_labels_have_both_languages_and_no_dash() -> None:
    en, fr = templates.labels()["en"], templates.labels()["fr"]
    assert set(en) == set(fr)
    for value in [*en.values(), *fr.values()]:
        for text in value if isinstance(value, list) else [value]:
            assert not re.search(r"[–—]| - ", text)


def test_contact_lines_link_only_https_and_mailto() -> None:
    view = ProfileView(
        revision=1,
        identity=Identity(
            full_name="Alex Example",
            email="e2e-alex@example.org",
            phone="+33 6 00 00 00 00",
            city="Lyon",
            country="France",
            linkedin_url="https://www.linkedin.example/in/alex/",
            website="http://example.org/alex",
            links=(
                ("Code", "javascript:alert(1)"),
                ("Same", "https://www.linkedin.example/in/alex/"),
            ),
        ),
    )
    assert templates.contact_lines(view) == [
        {"text": "e2e-alex@example.org", "href": "mailto:e2e-alex@example.org"},
        {"text": "+33 6 00 00 00 00", "href": None},
        {"text": "Lyon, France", "href": None},
        {"text": "linkedin.example/in/alex", "href": "https://www.linkedin.example/in/alex/"},
        {"text": "example.org/alex", "href": None},
        {"text": "javascript:alert(1)", "href": None},
    ]


def built(content: dict[str, Any], view: ProfileView, kind: str = "cv", lang: str = "en") -> Page:
    html = templates.build_html(template("classic"), kind, content, view, lang, 0, ON)
    return assert_authoring(html)


def test_an_employer_with_no_end_runs_to_present_and_one_left_out_is_not_printed() -> None:
    view = view_of("alex")
    content = sample("cv") | {
        "experience": [{"employer_key": "example-corp", "bullets": ["Ran it."]}]
    }
    page = built(content, view)
    assert "Example Corp" in page.body
    assert "January 2023 to present" in page.body
    assert "Sample Labs" not in page.body
    unknown = sample("cv") | {"experience": [{"employer_key": "nowhere-inc", "bullets": ["Lost."]}]}
    assert "Lost." not in built(unknown, view).body


def test_a_school_with_no_dates_and_a_year_alone() -> None:
    view = ProfileView(
        revision=1,
        identity=Identity(full_name="Alex Example"),
        schools=(
            School("Undated School", "Certificate", None, None, ""),
            School("Year School", "Diploma", "2019", "2019", ""),
        ),
    )
    page = built(sample("cv"), view)
    assert "Undated School" in page.body
    assert "Year School" in page.body
    assert "Diploma 2019" in page.body.replace("Year School ", "")


def test_six_languages_are_printed() -> None:
    names = ("French", "English", "German", "Spanish", "Italian", "Portuguese")
    view = ProfileView(
        revision=1,
        identity=Identity(full_name="Alex Example"),
        languages=tuple(Language(name, "fluent") for name in names),
    )
    body = built(sample("cv"), view).body
    assert all(re.search(rf"{name} ?: fluent", body) for name in names)


def test_right_to_left_text_in_a_bullet_prints_with_its_fallback_font() -> None:
    content = sample("cv") | {
        "experience": [{"employer_key": "example-corp", "bullets": ["Built שלום and مرحبا tools."]}]
    }
    html = templates.build_html(template("classic"), "cv", content, view_of("alex"), "en", 0, ON)
    assert "Built שלום and مرحبا tools." in Page(html).body
    assert ':root{font-family:"Nunito Sans","Noto Sans Arabic","Noto Sans Hebrew"}' in html


def test_latin_text_embeds_no_fallback_font() -> None:
    html = templates.build_html(
        template("classic"), "cv", sample("cv"), thumbnails.sample_view(), "en", 0, ON
    )
    assert "Noto" not in html
    assert html.count("@font-face") <= 10


def test_fixed_facts_come_from_the_base_cv_when_the_profile_has_none() -> None:
    base = base_cv("alex")
    view = profile_view.build({}, revision=1, base=base)
    employer = base.fixed.employers[0]
    content = sample("cv") | {
        "experience": [{"employer_key": employer.key, "bullets": ["Did it."]}]
    }
    body = built(content, view).body
    assert employer.organisation in body
    if employer.title:
        assert employer.title in body
    for school in base.fixed.schools:
        assert school.school in body


def test_french_labels_and_date() -> None:
    page = built(sample("cv"), thumbnails.sample_view(), "cv", "fr")
    assert "Expérience" in page.body
    assert "février 2023 à aujourd'hui" in page.body
    letter = built(sample("letter"), thumbnails.sample_view(), "letter", "fr")
    assert "14 octobre 2026" in letter.body
    assert "Objet :" in " ".join(letter.text)


def test_empty_sections_are_not_printed() -> None:
    content = {"schema": 1, "experience": [], "highlights": [], "competencies": [], "skills": []}
    view = ProfileView(revision=1, identity=Identity(full_name="Alex Example"))
    body = built(content, view).body
    labels = templates.labels()["en"]
    for key in ("summary", "experience", "education", "skills", "languages"):
        assert labels[key] not in body


def test_build_html_takes_a_step_object_or_its_index() -> None:
    found, view = template("classic"), thumbnails.sample_view()
    last = found.steps("cv")[-1]
    by_index = templates.build_html(found, "cv", sample("cv"), view, "en", -1, ON)
    assert by_index == templates.build_html(found, "cv", sample("cv"), view, "en", last, ON)
    assert last.css() in by_index


# --- the photo ----------------------------------------------------------------------------------


def jpeg_with_gps(size: tuple[int, int], quality: int = 95) -> bytes:
    image = Image.effect_noise(size, 90).convert("RGB")
    exif = Image.Exif()
    exif[0x010F] = "ExampleCameraMaker"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (45.0, 46.0, 1.0)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality, exif=exif.tobytes())
    return out.getvalue()


def decoded(uri: str) -> Image.Image:
    head, _, data = uri.partition(",")
    assert head in ("data:image/jpeg;base64", "data:image/png;base64")
    return Image.open(io.BytesIO(base64.b64decode(data)))


def test_a_photo_of_two_megabytes_is_resized_and_stripped() -> None:
    data = jpeg_with_gps((1600, 1200))
    assert 1_000_000 < len(data) <= 2 * 1024 * 1024
    assert b"ExampleCameraMaker" in data
    uri = photo.embed(data)
    image = decoded(uri)
    assert max(image.size) == photo.MAX_SIDE
    assert image.size == (600, 450)
    assert not image.getexif()
    assert "icc_profile" not in image.info
    assert b"ExampleCameraMaker" not in base64.b64decode(uri.split(",", 1)[1])
    assert len(uri) < photo.MAX_BYTES * 4 // 3 + 64


def test_a_small_png_photo_stays_png_and_small() -> None:
    out = io.BytesIO()
    Image.new("RGBA", (300, 400), (10, 120, 200, 128)).save(out, format="PNG")
    image = decoded(photo.embed(out.getvalue()))
    assert image.format == "PNG"
    assert image.size == (300, 400)


@pytest.mark.parametrize(
    "data",
    [b"", b"%PDF-1.7 not an image", b"<svg xmlns='x'></svg>", b"\x89PNG\r\n\x1a\n" + b"\0" * 64],
)
def test_a_photo_that_is_not_an_image_is_refused(data: bytes) -> None:
    with pytest.raises(photo.PhotoError):
        photo.embed(data)


def test_a_photo_over_the_cap_is_refused() -> None:
    with pytest.raises(photo.PhotoError) as raised:
        photo.embed(b"\xff\xd8\xff" + b"\0" * (2 * 1024 * 1024))
    assert raised.value.status == 413


def test_the_photo_prints_in_a_template_with_a_slot() -> None:
    uri = photo.embed(jpeg_with_gps((800, 800), quality=80))
    html = templates.build_html(
        template("classic"), "cv", sample("cv"), thumbnails.sample_view(), "en", 0, ON, photo=uri
    )
    images = [attrs for tag, attrs in assert_authoring(html).tags if tag == "img"]
    assert [attrs["src"] for attrs in images] == [uri]


def test_a_photo_that_is_not_an_embedded_address_is_refused() -> None:
    with pytest.raises(ValueError, match="not a data: address"):
        templates.build_html(
            template("classic"),
            "cv",
            sample("cv"),
            thumbnails.sample_view(),
            "en",
            0,
            ON,
            photo="https://example.org/me.jpg",
        )


# --- the font library ---------------------------------------------------------------------------

FAMILIES = {
    "nunito-sans": "Nunito Sans",
    "inter": "Inter",
    "source-serif-4": "Source Serif 4",
    "ibm-plex-sans": "IBM Plex Sans",
    "ibm-plex-mono": "IBM Plex Mono",
    "work-sans": "Work Sans",
    "eb-garamond": "EB Garamond",
    "lora": "Lora",
    "dm-sans": "DM Sans",
    "playfair-display": "Playfair Display",
    "carlito": "Carlito",
    "caladea": "Caladea",
    "arimo": "Arimo",
    "tinos": "Tinos",
}


def test_the_font_library_holds_the_fourteen_families_with_their_licences() -> None:
    library = templates.fonts()
    own = {key: family.name for key, family in library.items() if not family.fallback}
    assert own == FAMILIES
    for key, family in library.items():
        folder = templates.FONTS / key
        assert family.licence in templates.LICENCES
        assert "Open Font License" in (folder / "LICENSE.txt").read_text(encoding="utf-8")
        assert family.faces
        for face in family.faces:
            assert face.path.is_file()
            assert face.path.read_bytes()[:4] == b"wOF2"
        if not family.fallback:
            weights = {(face.weight, face.style) for face in family.faces}
            assert {(400, "normal"), (700, "normal")} <= weights, key
    assert {family.name for family in library.values() if family.fallback} == {
        "Noto Sans Hebrew",
        "Noto Sans Arabic",
        "Noto Emoji",
    }


def test_font_css_embeds_only_the_faces_the_text_uses() -> None:
    latin = templates.font_css(("nunito-sans",), "Hello")
    assert latin.count("@font-face") == 5  # 400, 400 italic, 600, 700, 700 italic of latin
    assert "Noto" not in latin
    emoji = templates.font_css(("nunito-sans",), "Hello 😀")
    assert ':root{font-family:"Nunito Sans","Noto Emoji"}' in emoji
    extended = templates.font_css(("inter",), "Łódź")
    assert extended.count("@font-face") == 10  # latin and latin-ext


# --- resolve --------------------------------------------------------------------------------------


def test_resolve_finds_a_bundled_template_then_asks_the_loader() -> None:
    asked: list[str] = []

    def loader(db: object, user_id: object, slug: str) -> templates.Template | None:
        asked.append(slug)
        return template("classic") if slug == "upload-1a2b3c4d" else None

    assert templates.resolve(None, None, "classic") is template("classic")
    assert templates.resolve(None, None, "upload-1a2b3c4d") is None  # no loader yet: a no op
    templates.register_loader(loader)
    try:
        assert templates.resolve(None, None, "classic") is template("classic")
        assert templates.resolve(None, None, "upload-1a2b3c4d") is template("classic")
        assert templates.resolve(None, None, "upload-ffffffff") is None
    finally:
        templates.register_loader(None)
    assert asked == ["upload-1a2b3c4d", "upload-ffffffff"]


# --- through the real renderer ------------------------------------------------------------------

docker = pytest.mark.docker


# D3's stack of the renderer (its fixture, under this module's name): one per module.
renderer = pytest.fixture(scope="module", name="renderer")(renderer_stack.__wrapped__)


def fitted(
    renderer: Any,
    found: templates.Template,
    kind: str,
    content: Any,
    view: ProfileView,
    language: str,
) -> fit.Fitted:
    async def render(html: str) -> Rendered:
        return await asyncio.to_thread(renderer.render, html)

    def build(step: templates.Step) -> str:
        return templates.build_html(found, kind, content, view, language, step, ON)

    return asyncio.run(fit.fit(found.steps(kind), build, render=render))


def pdf_fonts(pdf: bytes) -> list[str]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf)
    try:
        return render_client.fonts_of(document)
    finally:
        document.close()


def pdf_text(pdf: bytes) -> str:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf)
    try:
        return "".join(page.get_textpage().get_text_range() for page in document)
    finally:
        document.close()


@docker
@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_the_fit_rule_on_d1_fixtures(renderer: Any, slug: str, language: str) -> None:
    found, view = template(slug), d1_view()
    last = len(found.steps("cv")) - 1
    steps = {}
    for name in (*D1_CVS, *D1_LETTERS):
        kind, _, content = d1(name)
        result = fitted(renderer, found, kind, content, view, language)
        assert result.fit, f"{slug} {name} {language}: {result.pages} pages at the tightest"
        steps[name] = result.step
        main = templates.fonts()[found.fonts[0]].name.replace(" ", "")
        assert any(font.startswith(main) for font in pdf_fonts(result.pdf)), name
        assert "Alex Example" in pdf_text(result.pdf)
    assert steps["cv-short"] == 0, steps
    assert steps["cv-typical"] < last, steps
    assert steps["cv-short"] <= steps["cv-typical"] <= steps["cv-long"] <= steps["cv-tightest"]


@docker
@pytest.mark.parametrize("slug", SLUGS)
def test_the_fit_rule_on_d2_fixtures(renderer: Any, slug: str) -> None:
    found, view = template(slug), view_of("alex")
    for name in D2_FIXTURES:
        kind = "letter" if name.startswith("letter") else "cv"
        language = "fr" if name.endswith("_fr") else "en"
        result = fitted(renderer, found, kind, written(name), view, language)
        assert result.fit, f"{slug} {name}: {result.pages} pages at the tightest"


@docker
@pytest.mark.parametrize("slug", SLUGS)
def test_pages_never_grow_as_the_steps_tighten(renderer: Any, slug: str) -> None:
    found, view = template(slug), d1_view()
    for name in ("cv-long", "cv-tightest"):
        kind, language, content = d1(name)
        pages = [
            renderer.render(
                templates.build_html(found, kind, content, view, language, step, ON)
            ).pages
            for step in found.steps(kind)
        ]
        assert pages == sorted(pages, reverse=True), (name, pages)


@docker
@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("kind", templates.KINDS)
def test_hostile_text_is_inert_in_the_pdf(renderer: Any, slug: str, kind: str) -> None:
    content = hostile_cv() if kind == "cv" else hostile_letter()
    html = templates.build_html(template(slug), kind, content, hostile_view(), "en", -1, ON)
    rendered = renderer.render(html)  # the font check of D3 included
    text = "".join(pdf_text(rendered.pdf).split())
    assert "<script>alert(1)</script>" in text
    assert "</style><b>bold</b>" in text
    for marker in (b"/JavaScript", b"/JS ", b"/Launch", b"javascript:"):
        assert marker not in rendered.pdf, marker
    assert set(re.findall(rb"/URI\s*\(([^)]*)\)", rendered.pdf)) <= {b"mailto:e2e-alex@example.org"}


def at_soft_limits(content: c.CvContent, limits: Any) -> c.CvContent:
    """The typical fixture with every field the manifest counts filled to its soft limit."""

    def filled(text: str, size: int | None) -> str:
        if not size:
            return text
        words = (text + " ") * (size // max(len(text), 1) + 1)
        return words[:size].rstrip()

    data = content.model_dump()
    data["headline"] = filled(data["headline"], limits.headline)
    data["summary"] = filled(data["summary"], limits.summary)
    data["highlights"] = [filled(h, limits.highlight) for h in data["highlights"]]
    data["competencies"] = [filled(k, limits.competency) for k in data["competencies"]]
    for index, role in enumerate(data["experience"]):
        size = limits.main_bullet if index == 0 else limits.bullet
        role["bullets"] = [filled(b, size) for b in role["bullets"]]
    return c.CvContent.model_validate(data)


@docker
@pytest.mark.parametrize("slug", SLUGS)
def test_the_typical_cv_at_its_soft_limits_fits_before_the_last_step(
    renderer: Any, slug: str
) -> None:
    found = template(slug)
    _, language, content = d1("cv-typical")
    full = at_soft_limits(content, found.kinds["cv"].soft_limits)
    result = fitted(renderer, found, "cv", full, d1_view(), language)
    assert result.fit
    assert result.step < len(found.steps("cv")) - 1, result.step
