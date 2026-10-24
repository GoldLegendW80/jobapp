"""template_rules (task D25a): the checks of a template the design model writes.

The true inputs are the bundled templates: each passes untouched, or is refused only for the
overlay family (OVERLAY_FAMILY), which the static filter refuses whole since round 3; the
repository templates that use more are pinned to exactly what they use (REPOSITORY). The
refusals are classic's files with one change each, from the spec's step 4 and its neighbours: a
script, an outside address, `@import`, a literal sentence in every place a template can hold one
(HTML, a Jinja constant, a CSS `content`, an escape), a missing field and a field printed only
under another test, a CSS that ignores or pins the density steps, and what can lay a box over
another.
"""

import json
import random
import secrets

import pytest
from jinja2 import Environment, nodes

from app.documents import content as c
from app.documents import design_check, template_css, template_rules, templates
from app.documents.template_rules import FIELDS, LABELS, MACROS, Problem, check, check_css

CLASSIC = templates.ROOT / "classic"
MANIFEST = json.loads((CLASSIC / "manifest.json").read_text(encoding="utf-8"))
FILES = {name: (CLASSIC / name).read_text(encoding="utf-8") for name in templates.FILES}


def _check(files=None, accent=None) -> list[Problem]:
    return check(
        files or FILES,
        photo=MANIFEST["photo"],
        fonts=tuple(MANIFEST["fonts"]),
        accent=accent or MANIFEST["accent"],
    )


def _bundled():
    for folder in sorted(templates.ROOT.iterdir()):
        if folder.is_dir() and not folder.name.startswith(("_", ".")):
            yield folder


# The bundled templates are written by hand and never go through these checks in production;
# some use what can lay a box over another, which a template from an upload may not.
OVERLAY_FAMILY = ("it can lay a box over another", "a ::before or ::after holds punctuation only")
WHOLE = {"classic", "compact", "grid"}

# The repository's own templates need not pass the filter of an upload (decision on #427):
# each is pinned to exactly what it uses, its HTML set holding for cv.html.j2 and letter.html.j2.
_CUT = ("literal", "markup cut by a Jinja construct")
_OUTSIDE = ("property", "a property outside the allowed list")
_SELECTOR = ("selector", "a selector outside the allowed forms")
# A full bleed sheet sets its own page margin, executive's band a fixed height and its headline
# an inline size containment, sidebar's column a page high minimum: a template from an upload may
# not, the render check lays out the step's page box and its canary shapes only.
_PAGE = ("page", "@page: margin: var(--margin)")
_HEIGHT = ("property", "height: auto, a length only on the photo (img or .photo)")
_MIN_HEIGHT = ("property", "min-height: auto, a length only on the photo (img or .photo)")
# banner and sidebar are pinned in REPOSITORY below, with the rest of what they use
GEOMETRY = {
    "executive": {_HEIGHT, ("property", "contain: a value outside the allowed list")},
}
# the helpers of _base/layout.html.j2, which only the repository's templates import
_LAYOUT = {
    ("construct", "import only _base/macros.html.j2 as base with context"),
}
# the contact lines printed by those helpers only, which an upload may not call
_LAYOUT_CONTACT = _LAYOUT | {
    ("construct", "a call of anything but a macro of base"),
    ("unprinted", "contact[].text is not printed, or only under another test"),
}


def _box(*properties):
    return {
        ("property", f"{p}: a ::before or ::after holds punctuation only, no box")
        for p in properties
    }


def _over(*properties):
    return {("property", f"{p}: it can lay a box over another") for p in properties}


REPOSITORY = {
    "academic": (set(), {_SELECTOR}),
    # page 1's margin, which the band bleeds into
    "banner": (
        set(),
        {
            ("page", "@page :first: the repository's templates only"),
            ("page", "@page: only size: A4 and margin: var(--margin)"),
        },
    ),
    "bold": (
        {("construct", "the expression Compare"), _CUT},
        {
            _OUTSIDE,
            ("property", "grid-template-columns: a function outside the allowed list"),
            ("property", "margin: a value outside the allowed list"),
        }
        | _box("border"),
    ),
    "cards": (
        set(),
        {("property", "margin: a value outside the allowed list")}
        | _box("border", "border-radius"),
    ),
    "columns-header": (
        _LAYOUT_CONTACT,
        {("property", "grid-template-columns: a function outside the allowed list")},
    ),
    "elegant": (
        set(),
        {_OUTSIDE, ("property", "position: static or relative"), _PAGE}
        | _box("border-bottom", "flex")
        | _over("bottom", "grid-row", "left", "right", "top"),
    ),
    "nordic": (set(), _box("border-top", "width")),
    # a negative margin and `overflow: hidden` clip the rule that would open a row of contact lines
    "plain": (
        set(),
        {_SELECTOR, _OUTSIDE, ("property", "margin: a value outside the allowed list")},
    ),
    "portrait": (
        _LAYOUT
        | {("construct", "the construct Assign"), _CUT, ("name", "a name that is not a field")},
        {
            _OUTSIDE,
            _SELECTOR,
            _HEIGHT,
            ("property", "vertical-align: a value outside the allowed list"),
        }
        | _box("border", "border-top", "height", "width")
        | _over("grid-row"),
    ),
    "sidebar": (_LAYOUT_CONTACT, {_PAGE, _MIN_HEIGHT} | _over("grid-area")),
    "split-right": (_LAYOUT_CONTACT, _over("grid-area") | _box("border-top", "width")),
}


def _hand_written():
    return [f for f in _bundled() if f.name not in REPOSITORY]


@pytest.mark.parametrize("name", sorted(REPOSITORY))
def test_each_repository_template_shows_exactly_what_it_uses(name):
    folder = templates.ROOT / name
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    files = {file: (folder / file).read_text(encoding="utf-8") for file in templates.FILES}
    found = check(
        files,
        photo=manifest["photo"],
        fonts=tuple(manifest["fonts"]),
        accent=manifest["accent"],
    )
    html, css = REPOSITORY[name]
    expected = {(file, *problem) for file in ("cv.html.j2", "letter.html.j2") for problem in html}
    expected |= {("style.css", *problem) for problem in css}
    assert {(p.file, p.code, p.detail) for p in found} == expected


@pytest.mark.parametrize("folder", _hand_written(), ids=lambda f: f.name)
def test_every_bundled_template_passes_untouched_but_for_the_overlay_family(folder):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    files = {name: (folder / name).read_text(encoding="utf-8") for name in templates.FILES}
    found = check(
        files,
        photo=manifest["photo"],
        fonts=tuple(manifest["fonts"]),
        accent=manifest["accent"],
    )
    pinned = GEOMETRY.get(folder.name, set())
    assert {(p.code, p.detail) for p in found if (p.code, p.detail) in pinned} == pinned
    found = [p for p in found if (p.code, p.detail) not in pinned]
    assert (found == []) == (folder.name in WHOLE)
    assert [p for p in found if p.file != "style.css" or p.code != "property"] == []
    assert all(p.detail.split(": ", 1)[1].startswith(OVERLAY_FAMILY) for p in found)


def test_the_macro_table_is_the_shared_macros_and_the_labels_exist():
    tree = Environment(autoescape=True).parse(
        (templates.BASE / "macros.html.j2").read_text(encoding="utf-8")
    )
    found = {m.name: tuple(a.name for a in m.args) for m in tree.find_all(nodes.Macro)}
    assert found == {name: tuple(p for p, _ in params) for name, params in MACROS.items()}
    for language in templates.LANGUAGES:
        assert all(isinstance(templates.labels()[language][key], str) for key in LABELS)


def test_the_fields_are_those_of_the_content_and_never_the_unprinted_ones():
    for kind, model in (("cv", c.CvContent), ("letter", c.LetterContent)):
        named = {p.split(".")[1].split("[")[0] for p in FIELDS[kind] if p.startswith("content.")}
        assert named <= set(model.model_fields)
        assert not named & {"target", "notes", "experience"}


def _cv(old: str, new: str, count: int = 1) -> dict[str, str]:
    assert old in FILES["cv.html.j2"]
    return {**FILES, "cv.html.j2": FILES["cv.html.j2"].replace(old, new, count)}


def _letter(old: str, new: str) -> dict[str, str]:
    assert old in FILES["letter.html.j2"]
    return {**FILES, "letter.html.j2": FILES["letter.html.j2"].replace(old, new, 1)}


SUMMARY = '<p class="summary">{{ content.summary }}</p>'
ROLE = "{{ base.employer(role) }}"
ROLES = "{% for role in roles %}\n{{ base.employer(role) }}\n{% endfor %}"
HTML_REFUSED = [
    # (the file changed, the code expected)
    (_cv(SUMMARY, SUMMARY + "<script>x()</script>"), "tag"),
    (_cv(SUMMARY, SUMMARY + '<img src="https://example.org/a.png">'), "authoring"),
    (_cv(SUMMARY, '<p class="summary" onclick="x()">{{ content.summary }}</p>'), "authoring"),
    (_cv(SUMMARY, SUMMARY + "<p>Led a team of twelve engineers</p>"), "literal"),
    (_cv(SUMMARY, SUMMARY + "<p>Ingénieure principale</p>"), "literal"),
    (_cv(SUMMARY, SUMMARY + "<p>12</p>"), "literal"),
    (_cv(SUMMARY, SUMMARY + "<p>★★★★☆</p>"), "literal"),
    (_cv(SUMMARY, '<p class="summary">{{ name }} – {{ content.summary }}</p>'), "literal"),
    (_cv(SUMMARY, '<p class="summary">{{ name }} - {{ content.summary }}</p>'), "literal"),
    (_cv(SUMMARY, SUMMARY + "<!-- written for Example Corp -->"), "literal"),
    (_cv(SUMMARY, '<p class="summary" title="Senior">{{ content.summary }}</p>'), "attribute"),
    (_cv(SUMMARY, '<p class="summary" style="color:#000">{{ content.summary }}</p>'), "attribute"),
    (_cv(SUMMARY, '<p class="{{ content.headline }}">{{ content.summary }}</p>'), "attribute"),
    (_cv(SUMMARY, '<p class="Summary Box">{{ content.summary }}</p>'), "attribute"),
    (_cv(SUMMARY, SUMMARY + '<a class="x">{{ name }}</a>'), "tag"),
    (_cv(SUMMARY, SUMMARY + "<svg><text>{{ name }}</text></svg>"), "tag"),
    (_cv(SUMMARY, SUMMARY + '{{ "Led a team of 12" }}'), "constant"),
    (_cv(SUMMARY, SUMMARY + "{{ 12 }}"), "constant"),
    (_cv(SUMMARY, SUMMARY + '{% set claim = "Led a team" %}{{ claim }}'), "construct"),
    (_cv(SUMMARY, SUMMARY + '{% include "_base/page.html.j2" %}'), "construct"),
    (_cv(SUMMARY, SUMMARY + "{% macro m() %}{% endmacro %}"), "construct"),
    (_cv(SUMMARY, SUMMARY + "{% filter upper %}{{ name }}{% endfilter %}"), "construct"),
    (_cv('base.label("summary")', 'base.label("Senior engineer")'), "constant"),
    (_cv('base.label("summary")', 'base.label("months")'), "constant"),
    (_cv('base.label("summary")', "base.label(name)"), "constant"),
    (
        _cv("base.header(content.headline)", 'base.header("Senior engineer at Example Corp")'),
        "constant",
    ),
    (
        _cv('content.competencies, "competencies")', 'content.competencies, "Led Teams")'),
        "constant",
    ),
    (_cv(SUMMARY, SUMMARY + "{{ content.target }}"), "name"),
    (_cv(SUMMARY, SUMMARY + "{{ content.notes }}"), "name"),
    (_cv(SUMMARY, SUMMARY + "{{ content.__class__ }}"), "name"),
    (_cv(SUMMARY, SUMMARY + "{{ labels.summary }}"), "name"),
    (_cv(SUMMARY, SUMMARY + "{{ content.skills[0].label }}"), "name"),
    (_cv(SUMMARY, SUMMARY + "{{ content.highlights }}"), "construct"),
    (_cv(SUMMARY, SUMMARY + "{{ photo }}"), "construct"),
    (_cv(SUMMARY, SUMMARY + "{{ name | upper }}"), "construct"),
    (_cv(SUMMARY, SUMMARY + '{{ content.highlights | join(" and ") }}'), "construct"),
    (_cv(SUMMARY, SUMMARY + "{{ base.employer }}"), "name"),
    (_cv(SUMMARY, SUMMARY + "{{ content.summary if name }}"), "construct"),
    (_cv(SUMMARY, SUMMARY + '{% if name == "Alex" %}{% endif %}'), "construct"),
    (_cv(SUMMARY, SUMMARY + "{% for x in name %}{% endfor %}"), "construct"),
    (_cv(SUMMARY, SUMMARY + "{% for r in roles if r.title %}{% endfor %}"), "construct"),
    (_cv(ROLE, "{{ loop.index }}" + ROLE), "name"),
    (_cv('{% extends "_base/page.html.j2" %}', '{% extends "other.html" %}'), "construct"),
    (_cv(" as base with context", " as base"), "construct"),
    (_cv("{% block body %}", "{% block body %}{% block extra %}{% endblock %}"), "construct"),
    (_cv(SUMMARY, SUMMARY + "{% if %}"), "syntax"),
    (_letter("{{ base.letter_date() }}", "{{ base.letter_date() }}<p>Paris</p>"), "literal"),
]


@pytest.mark.parametrize(("files", "code"), HTML_REFUSED)
def test_a_template_that_breaks_a_rule_is_refused_with_its_code(files, code):
    assert code in {problem.code for problem in _check(files)}


UNPRINTED = [
    # (the change, the file, the field then missing)
    (_cv(ROLE, ""), "cv", "roles[].title"),
    (_cv(ROLE, "{% if photo %}" + ROLE + "{% endif %}"), "cv", "roles[].organisation"),
    (_cv(ROLE, "{% if not roles %}" + ROLE + "{% endif %}"), "cv", "roles[].period"),
    (_cv(ROLE, "{% if roles and photo %}" + ROLE + "{% endif %}"), "cv", "roles[].bullets"),
    (_cv(ROLE, "{% if roles and role.title %}" + ROLE + "{% endif %}"), "cv", "roles[].location"),
    (
        _cv(ROLE, "{% if (roles and photo) or content.summary %}" + ROLE + "{% endif %}"),
        "cv",
        "roles[].title",
    ),
    (_cv(ROLE, "{% if not (roles or schools) %}" + ROLE + "{% endif %}"), "cv", "roles[].title"),
    (_cv(ROLE, "{% if roles %}{% else %}" + ROLE + "{% endif %}"), "cv", "roles[].location"),
    (_cv(ROLE, "{% if content.summary %}" + ROLE + "{% endif %}"), "cv", "roles[].title"),
    (_cv(ROLE, "{% if loop.first %}" + ROLE + "{% endif %}"), "cv", "roles[].title"),
    (_cv(SUMMARY, ""), "cv", "content.summary"),
    (
        _cv(
            SUMMARY,
            '<p class="summary">{% for h in content.highlights %}{{ content.summary }}'
            "{% endfor %}</p>",
        ),
        "cv",
        "content.summary",
    ),
    (
        _cv("{{ base.header(content.headline) }}", "{{ base.header(content.summary) }}"),
        "cv",
        "content.headline",
    ),
    (_cv("{{ base.skill_group(group) }}", "{{ group.label }}"), "cv", "content.skills[].items"),
    (_letter("{{ base.paragraphs(content.paragraphs) }}", ""), "letter", "content.paragraphs"),
    (_letter("{{ base.letter_date() }}", ""), "letter", "date"),
    (_letter("{{ base.header(content.headline) }}", ""), "letter", "contact[].text"),
]


@pytest.mark.parametrize(("files", "kind", "field"), UNPRINTED)
def test_a_field_not_printed_or_printed_only_under_another_test_is_refused(files, kind, field):
    expected = f"{field} is not printed, or only under another test"
    assert Problem(f"{kind}.html.j2", 0, "unprinted", expected) in _check(files)


HTML_ACCEPTED = [
    _cv(ROLE, "{% if roles or schools %}" + ROLE + "{% endif %}"),
    _cv(ROLE, "{% if photo or (roles and role) %}" + ROLE + "{% endif %}"),
    _cv(ROLE, "{% if (roles or schools) and role %}" + ROLE + "{% endif %}"),
    _cv(
        ROLE,
        '<div class="entry"><h3>{{ role.title }}, {{ role.organisation }}</h3>'
        "<p>{{ role.period }}{% if role.location %}, {{ role.location }}{% endif %}</p>"
        "<ul>{% for b in role.bullets %}<li>{{ b }}</li>{% endfor %}</ul></div>",
    ),
    _cv("{{ base.skill_group(group) }}", '{{ group.label }}: {{ group["items"] | join(", ") }}'),
    _cv(
        "{{ base.skill_group(group) }}",
        '{{ group.label }}{% for i in group["items"] %}{{ i }}'
        "{% if not loop.last %} · {% endif %}{% endfor %}",
    ),
    _cv(SUMMARY, '<p class="summary">&nbsp;{{ content.summary }} (&amp;) | /</p>'),
    _cv(SUMMARY, '<div class="a b-c d"><span class="sep">•</span>{{ content.summary }}</div>'),
    _cv(
        "{{ base.header(content.headline) }}",
        '{{ base.portrait(class="photo round") }}'
        "<h1>{{ name }}</h1><p>{{ content.headline }}</p>{{ base.contact_lines() }}",
    ),
    _cv('content.competencies, "competencies")', 'content.competencies, class="competencies")'),
]


@pytest.mark.parametrize("files", HTML_ACCEPTED)
def test_the_same_fields_written_another_way_pass(files):
    assert _check(files) == []


# sidebar's narrow column helpers each print the contact lines at some lengths only, so a
# template that calls one alone can drop them all: an upload may call neither
HEADER = "{{ base.header(content.headline) }}"
NO_CONTACT = "{{ base.portrait() }}<h1>{{ name }}</h1><p>{{ content.headline }}</p>"
NARROW_CALLS = [
    "{{ base.contact_column() }}",
    '{{ base.contact_column("label") }}',
    "{{ base.contact_across() }}",
    '{{ base.contact_across(class="contact across") }}',
    "{{ base.contact_across() }}{{ base.contact_column() }}",
]


@pytest.mark.parametrize("call", NARROW_CALLS)
@pytest.mark.parametrize("kind", ["cv", "letter"])
def test_an_upload_calling_the_narrow_column_contact_helpers_is_refused(kind, call):
    files = (_cv if kind == "cv" else _letter)(HEADER, NO_CONTACT + call)
    found = {(p.file, p.code, p.detail) for p in _check(files)}
    file = f"{kind}.html.j2"
    assert (file, "construct", "a call of anything but a macro of base") in found
    unprinted = "contact[].text is not printed, or only under another test"
    assert (file, "unprinted", unprinted) in found


def test_an_upload_importing_the_repository_helpers_is_refused():
    base = '{% import "_base/macros.html.j2" as base with context %}'
    files = _cv(HEADER, NO_CONTACT + "{{ layout.contact_across() }}{{ layout.contact_column() }}")
    layout = '\n{% import "_base/layout.html.j2" as layout with context %}'
    files["cv.html.j2"] = files["cv.html.j2"].replace(base, base + layout)
    codes = {(p.code, p.detail) for p in _check(files)}
    assert ("construct", "import only _base/macros.html.j2 as base with context") in codes
    assert ("construct", "a call of anything but a macro of base") in codes
    assert ("unprinted", "contact[].text is not printed, or only under another test") in codes


def _css(addition: str, old: str = "", new: str = "") -> dict[str, str]:
    sheet = FILES["style.css"]
    if old:
        assert old in sheet
        sheet = sheet.replace(old, new)
    return {**FILES, "style.css": sheet + "\n" + addition}


CSS_REFUSED = [
    (_css('@import "x.css";'), "authoring"),
    (_css(".x { background: url(https://example.org/a.png); }"), "authoring"),
    (_css("@media print { .x { color: #000000; } }"), "structure"),
    (_css(".x { color: #000000; "), "structure"),
    (_css(".x { \\63 olor: #000000; }"), "structure"),
    (_css('.x::before { content: "Senior engineer"; }'), "content"),
    (_css('.x::before { content: "\\53 enior"; }'), "content"),
    (_css(".x::before { content: attr(title); }"), "content"),
    (_css('.x::before { content: "\\2013"; }'), "content"),
    (_css('.x::before { content: "12"; }'), "content"),
    (_css('.x::before { content: "•" "Senior"; }'), "content"),
    (_css('.x::before { content: "•" attr(title); }'), "content"),
    (_css('.x { list-style-type: "Senior "; }'), "content"),
    (_css('.x { list-style: "\\2605  " inside; }'), "content"),
    (_css(".x { list-style: url(data:image/png;base64,AAAA); }"), "property"),
    (_css(":root { --body-size: 9pt; }"), "density_pinned"),
    (_css(":root { --accent: #ff0000; }"), "density_pinned"),
    (_css("", "font-size: var(--body-size);", "font-size: 10pt;"), "density_unread"),
    (_css("", "line-height: var(--line-height);", "line-height: 1.3;"), "density_unread"),
    (_css("", "var(--section-gap)", "8pt"), "density_unread"),
    (_css("", "var(--item-gap)", "4pt"), "density_unread"),
    (_css("@page { margin: 0; }"), "page"),
    (_css("@page { size: letter; }"), "page"),
    (_css("@page { size: A4; padding: 0; }"), "page"),
    (_css(".name { font-size: 24pt; }"), "size"),
    (_css(".name { font-size: 0em; }"), "size"),
    (_css(".name { font-size: 1.2rem; }"), "size"),
    (_css(".name { font-size: x-large; }"), "size"),
    (_css(".x { line-height: 12pt; }"), "size"),
    (_css(".x { letter-spacing: -2em; }"), "size"),
    (_css(".x { display: none; }"), "property"),
    (_css(".x { visibility: hidden; }"), "property"),
    (_css(".x { opacity: 0; }"), "property"),
    (_css(".x { overflow: hidden; height: 0; }"), "property"),
    (_css(".x { text-indent: -999em; }"), "property"),
    (_css(".x { transform: scale(0); }"), "property"),
    (_css(".x { font: 10pt Arial; }"), "property"),
    (_css(".x { position: fixed; }"), "property"),
    (_css(".x { position: absolute; left: -9999px; }"), "property"),
    (_css(".x { color: #ffffff; background: #ffffff; }"), "contrast"),
    (_css(".x { color: #fff; background-color: #fafafa; }"), "contrast"),
    (_css(".x { background: #ffffff; color: #aaaaaa; }"), "contrast"),
    (_css(".x { color: red; }"), "colour"),
    (_css(".x { color: rgba(0, 0, 0, 0); }"), "colour"),
    (_css(".x { color: transparent; }"), "colour"),
    (_css(".x { background: linear-gradient(#ffffff, #000000); }"), "colour"),
    (_css('.x { font-family: "Calibri"; }'), "font"),
    (_css(".x { font-family: Arial, sans-serif; }"), "font"),
    (_css('.x { font-family: "Inter", sans-serif; }'), "font"),
    (_css(".x { size: A4; }"), "property"),
]


@pytest.mark.parametrize(("files", "code"), CSS_REFUSED)
def test_a_style_sheet_that_breaks_a_rule_is_refused_with_its_code(files, code):
    assert code in {problem.code for problem in _check(files)}


CSS_ACCEPTED = [
    _css('.x::before { content: "\\00b7\\00a0"; } .y::after { content: "•"; }'),
    _css(".band { background: #1f4e79; } .band h2 { color: #ffffff; }"),
    _css(".band { background: var(--accent); } .band p { color: #dff3ef; }"),
    _css("@page { size: A4; margin: var(--margin); } .page { padding: 2mm; }"),
    _css(".x { color: var(--accent); font-size: 115%; line-height: 1.2; letter-spacing: 0.1em; }"),
    _css(".x { font-family: 'Nunito Sans', sans-serif; } .y { font-weight: 700 !important; }"),
    _css(":root { --rail: 27mm; } .x { grid-template-columns: var(--rail) minmax(0, 1fr); }"),
    _css("/* a comment with { braces } and ; */ .x { color: #1d232b; }"),
    _css(".x { position: relative; }"),
    _css(".x { list-style: square inside; } .y { list-style-type: none; }"),
]


@pytest.mark.parametrize("files", CSS_ACCEPTED)
def test_a_style_sheet_written_another_way_passes(files):
    assert _check(files) == []


def test_the_accent_is_held_to_the_same_contrast_as_any_text():
    files = _css(".band { background: #ffffff; color: var(--accent); }")
    assert {p.code for p in _check(files, accent="#f0f0f0")} == {"contrast"}
    assert _check(files, accent="#1F4E79") == []


def test_a_problem_never_carries_text_of_the_template():
    marker = "zq" + secrets.token_hex(6)
    files = _cv(
        SUMMARY,
        SUMMARY + f'<p title="{marker}">{marker}</p>{{{{ "{marker}" }}}}'
        f"{{{{ {marker} }}}}{{{{ content.{marker} }}}}",
    )
    files["style.css"] += (
        f'\n.{marker} {{ {marker}: 1; content: "{marker}"; font-family: "{marker}"; }}'
        f"\n@{marker} {{ }}"
    )
    found = _check(files)
    no_at_rule = {**files, "style.css": files["style.css"].replace(f"@{marker} {{ }}", "")}
    found += _check(no_at_rule)
    assert len(found) >= 8
    assert not [p for p in found if marker in str(p) or marker in p.code]
    assert {p.code for p in found} <= set(template_rules.CODES)


@pytest.mark.parametrize(
    ("files", "code"),
    [
        (_cv(SUMMARY, SUMMARY + "<p></p>" * 9200), "too_large"),
        ({**FILES, "style.css": FILES["style.css"] + ".x{}" * 16000}, "too_large"),
        (_cv(SUMMARY, SUMMARY + "{% if name %}" * 2000 + "{% endif %}" * 2000), "syntax"),
        (_cv(SUMMARY, SUMMARY + "{{ " + "(" * 3000 + "name" + ")" * 3000 + " }}"), "syntax"),
        (_cv(SUMMARY, SUMMARY + "{{ " + "9" * 5000 + " }}"), "syntax"),
        (_letter("{{ content.closing }}", "{% if " + "9" * 4301 + " %}{% endif %}"), "syntax"),
        (
            {**FILES, "style.css": FILES["style.css"] + ".x{grid-column:" + "9" * 400 + "}"},
            "property",
        ),
    ],
)
def test_the_largest_and_deepest_files_end_as_a_problem_not_an_error(files, code):
    assert code in {problem.code for problem in _check(files)}


def test_a_file_at_the_cap_of_the_shapes_the_rules_read_is_checked_whole():
    rows = '<div class="row"><p class="x">{{ content.summary }}</p></div>\n'
    sheet = ".x { color: #1d232b; margin: var(--section-gap) 0; }\n"
    files = _cv(SUMMARY, SUMMARY + rows * 900)
    files["style.css"] += sheet * 1000
    assert all(len(text) <= template_rules.FILE_MAX for text in files.values())
    assert _check(files) == []


# --- round 2 of the review: the reviewer's probes and their neighbours, never deleted ----------


def _body_outside_the_block() -> dict[str, str]:
    source = FILES["cv.html.j2"].replace("{% block body %}", "{% block body %}{% endblock %}", 1)
    head, _, _ = source.rpartition("{% endblock %}")
    return {**FILES, "cv.html.j2": head}


def _top(addition: str, before: bool = False) -> dict[str, str]:
    source = FILES["cv.html.j2"]
    return {**FILES, "cv.html.j2": addition + source if before else source + addition}


CUT = [
    # a tag, an attribute, a comment or a reference cut by a construct: the check would read a
    # class value where the render prints text
    '{% if name %}{% else %}<i class="{% endif %}senior staff engineer'
    '{% if name %}{% else %}">{% endif %}',
    '{% for h in content.highlights %}<b class="{% endfor %}led a team'
    '{% for h in content.highlights %}">{% endfor %}',
    '{% if name %}<i class="{% elif photo %}<b class="{% endif %}lead{% if name %}">{% endif %}',
    '{% for h in content.highlights %}{% else %}<i class="{% endfor %}x">',
    "{% if name %}<i class='{% endif %}Led a team{% if name %}'>{% endif %}",
    '<span class="a" {{ name }}></span>',
    '<span {% if name %}class="a"{% endif %}></span>',
    "{% if name %}<!--{% endif %}Led a team{% if name %}-->{% endif %}",
    '<p class="x">&am{% if name %}{% endif %}p;</p>',
    '<I CLASS="{% if name %}{% endif %}Lead">',
    '{% if name %}<p class="x{% endif %} y">',
]
JINJA_REFUSED = [
    *((_cv(SUMMARY, SUMMARY + probe), "literal") for probe in CUT),
    (_letter("{{ base.letter_date() }}", "{{ base.letter_date() }}" + CUT[0]), "literal"),
    (_body_outside_the_block(), "construct"),
    (_top("<p>Led a team</p>"), "construct"),
    (_top("{% if name %}{{ name }}{% endif %}"), "construct"),
    (_top("{% block head %}{% endblock %}"), "construct"),
    (_top("Led a team", before=True), "construct"),
    (_top('{% set x = "a" %}', before=True), "construct"),
    (_cv('{% extends "_base/page.html.j2" %}', ""), "construct"),
    (_cv("{% block body %}", "{% block body scoped %}"), "construct"),
    (_cv(SUMMARY, SUMMARY + '{{ content | join(", ") }}'), "construct"),
    (_cv(SUMMARY, SUMMARY + '{{ roles | join(", ") }}'), "construct"),
    (_cv(SUMMARY, SUMMARY + '{{ name | join(" ") }}'), "construct"),
    (_cv(SUMMARY, SUMMARY + '{{ photo | join("") }}'), "construct"),
    (_cv(SUMMARY, SUMMARY + '{{ content.skills | join(", ") }}'), "construct"),
    (_cv(SUMMARY, SUMMARY + '{{ languages | join(", ") }}'), "construct"),
    (_cv(ROLE, '{{ role | join(", ") }}' + ROLE), "construct"),
    (_cv(ROLE, '{{ role.title | join(", ") }}' + ROLE), "construct"),
    (_cv("{{ base.skill_group(group) }}", '{{ group.items | join(", ") }}'), "name"),
    (_cv("{{ base.skill_group(group) }}", "{% for i in group.items %}{{ i }}{% endfor %}"), "name"),
    (_cv(SUMMARY, SUMMARY + "{% for k in content.keys %}{% endfor %}"), "name"),
    (_cv(ROLE, "{{ role.get }}" + ROLE), "name"),
    (_cv(ROLE, "{{ role.values }}" + ROLE), "name"),
    (_cv(ROLE, "{% for h in content.highlights %}{{ h }}{% endfor %}" + ROLE), "construct"),
    (_cv(ROLE, "{% for r in roles %}{{ r.title }}{% endfor %}" + ROLE), "construct"),
    (_cv(ROLE, "{% for s in schools %}{{ s.school }}{% endfor %}" + ROLE), "construct"),
    (_cv(SUMMARY, SUMMARY + "<p>\ue000</p>"), "literal"),
]


@pytest.mark.parametrize(("files", "code"), JINJA_REFUSED)
def test_round_two_jinja_and_markup_probes_are_refused(files, code):
    assert code in {problem.code for problem in _check(files)}


def test_a_body_outside_the_block_is_not_counted_as_printed():
    expected = "content.summary is not printed, or only under another test"
    assert Problem("cv.html.j2", 0, "unprinted", expected) in _check(_body_outside_the_block())


RENDER_REFUSED = [
    # well formed for the source check, but the browser's tree differs from the one checked
    _cv(SUMMARY, '<p class="summary"><div class="x">{{ content.summary }}</div></p>'),
    _cv(SUMMARY, '<div class="x">' + SUMMARY),
    _cv(SUMMARY, SUMMARY + "</div>"),
    _cv(SUMMARY, '<div class="x"><span class="y">' + SUMMARY + "</div></span>"),
    _cv(SUMMARY, '<table class="t"><div class="x">' + SUMMARY + "</div></table>"),
    _cv(SUMMARY, '<table class="t"><tr><td>{{ content.summary }}</td></tr>, </table>'),
    _cv(SUMMARY, '<h2 class="a"><h3 class="b">{{ content.summary }}</h3></h2>'),
    _cv(SUMMARY, '<ul class="a"><li><li>{{ content.summary }}</li></li></ul>'),
    _cv(SUMMARY, "<br></br>" + SUMMARY),
    _cv(SUMMARY, '<td class="a">{{ content.summary }}</td>'),
]


@pytest.mark.parametrize("files", RENDER_REFUSED)
def test_a_template_the_browser_would_build_another_tree_from_is_refused(files):
    # since round 3 the rows unbalanced inside an if body are refused by the source check too
    assert "structure" in {problem.code for problem in _check(files)}


ROUND_TWO_ACCEPTED = [
    _cv(
        ROLE,
        '<div class="entry">{{ role.title }}, {{ role.organisation }}, {{ role.period }}, '
        "{{ role.location }}"
        '{% for b in role.bullets %}<p class="b">{{ b }}</p>{% endfor %}</div>',
    ),
    _cv(
        "{{ base.skill_group(group) }}",
        '<p class="g">{{ group.label }}: {% for i in group["items"] %}{{ i }}'
        "{% if not loop.last %}, {% endif %}{% endfor %}</p>",
    ),
    _cv(SUMMARY, '<table class="t"><tbody><tr><td>{{ content.summary }}</td></tr></tbody></table>'),
    _top("\n\n{# a comment #}\n"),
]


@pytest.mark.parametrize("files", ROUND_TWO_ACCEPTED)
def test_round_two_true_inputs_pass(files):
    assert _check(files) == []


SHEET_REFUSED = [
    # (the sheet added to classic's, the code expected)
    # the reviewer's probes: a url( and a comment the browser reads otherwise
    (".a{--z:url(data:\")}.summary::before{content:'Led a team of forty'}.c{--y:\"}", "property"),
    ('.a url(data:"){}.summary{display:none}.c{--y:"}', "property"),
    (".a{--z:url(data:/*)}.summary::before{content:'Led a team'}.c{--y:*/}", "property"),
    ('.a{--z:URL(data:")}.summary::before{content:"Lead"}.c{--y:"}', "property"),
    (".a{--z:Url(data:x)}", "property"),
    (".a{--z:url( data:x )}", "property"),
    ('.summary{background:url("data:image/png;base64,AAAA")}', "property"),
    # the reviewer's probes through var()
    (
        ':root{--x:"Led a team of forty "}.summary{display:list-item;list-style-type:var(--x)}',
        "content",
    ),
    (":root{--n:-9999px}.summary{position:absolute;left:var(--n)}", "property"),
    (
        ':root{--x:image-set("\\68ttps\\3a\\2f\\2f example.org\\2f a.png" 1x)}'
        ".summary{display:list-item;list-style:var(--x)}",
        "property",
    ),
    (":root{--x:none}.summary{display:var(--x)}", "property"),
    (":root{--x:#1d232b}:root{--x:#ffffff}.summary{color:var(--x)}", "property"),
    (".summary{font-size:var(--Body-Size)}", "size"),
    (".summary{font-size:var(--body-size, 9pt)}", "size"),
    (".summary{color:var(--undefined)}", "colour"),
    (":root{--rail:9999mm}", "property"),
    # the reviewer's contrast probes on one rule (those on another rule's background: below)
    (".summary { color: #1d232b; background: #1d232b; }", "contrast"),
    (".summary{color:var(--accent);background:var(--accent)}", "contrast"),
    # the follow ups: hidden by offsets, a generic family first, an at-rule that looks like @page
    (".summary { margin-left: -1000em; }", "property"),
    (".summary { position: relative; left: 9999em; }", "property"),
    (".summary { width: 5000mm; }", "property"),
    (".summary { font-family: serif; }", "font"),
    ('.summary { font-family: sans-serif, "Nunito Sans"; }', "font"),
    ("@pagex { size: A4; }", "structure"),
    # page 1's rule: `:first` written as CSS reads it, and nothing else
    ("@page :left { margin: 0; }", "structure"),
    ("@page : first { margin: 0; }", "structure"),
    ("@page :first:left { margin: 0; }", "structure"),
    ("@page .first { margin: 0; }", "structure"),
    ("@page :first { color: #000000; }", "page"),
    ("@page :first { size: letter; }", "page"),
    # page 1's rule holds the repository's templates only, whatever it sets
    ("@page :first { margin-top: 0; } .x { padding: var(--margin); }", "page"),
    ("@page:FIRST { size: A4; margin: 0 0 12mm; } .x { padding: var(--margin); }", "page"),
    (".x { box-decoration-break: auto; }", "property"),
    # selectors outside the allow list
    ("[title] { color: #000000; }", "structure"),
    (".x:not(.y) { color: #000000; }", "selector"),
    (".x:hover { color: #000000; }", "selector"),
    ("#id { color: #000000; }", "selector"),
    ("svg { color: #000000; }", "selector"),
    (".x::first-line { color: #000000; }", "selector"),
    (".a .b .c .d .e { color: #000000; }", "selector"),
    (".x, { color: #000000; }", "selector"),
    # input the tokenizer stops on
    (".x { color: #１２３４５６; }", "structure"),
    (".é { color: #000000; }", "structure"),
    (".x { col\\or: #000000; }", "structure"),
    (".x { color: #000000; } /* not closed", "structure"),
    ('.x::before { content: "•; }', "structure"),
    ('.x::before { content: "a\\\nb"; }', "structure"),
    # values outside the allow lists
    (".x { display: contents; }", "property"),
    (".x::before { content: counter(x); }", "content"),
    (".x { list-style-type: disclosure-open; }", "property"),
    (".x { grid-template-columns: repeat(2, 1fr); }", "property"),
    (".x { columns: 2 200mm; }", "property"),
    (".x { margin: 1fr; }", "property"),
    (".x { color: #000000 !ie; }", "colour"),
    (".x { width: minmax(0, 1fr); }", "property"),
    (".x { border: 1px solid red; }", "property"),
]


@pytest.mark.parametrize(("addition", "code"), SHEET_REFUSED)
def test_round_two_sheet_probes_are_refused(addition, code):
    assert code in {problem.code for problem in _check(_css(addition))}


SHEET_ACCEPTED = [
    '.x::before { content: "\\00B7"; }',
    ".a > .b + .c ~ .d { color: #1d232b; }",
    ".x { border: 1px solid #1d232b; } .y { border-bottom: 0; }",
    "@PAGE { size: A4; }",
    ".x { box-decoration-break: clone; } .y { box-decoration-break: slice; }",
    ".x { grid-template-columns: minmax(0, 1fr) 58mm; grid-column: 1 / -1; }",
    ".x:last-child { color: #1d232b; }",
    ".summary { background: #1f4e79; color: #ffffff; }",
]


@pytest.mark.parametrize("addition", SHEET_ACCEPTED)
def test_round_two_sheets_written_another_way_pass(addition):
    assert _check(_css(addition)) == []


def test_page_one_rule_is_refused_while_the_rest_of_the_sheet_is_still_checked():
    found = _check(_css("@page :first { margin-top: 0; } .x { position: absolute; }"))
    pages = [p.detail for p in found if p.code == "page"]
    assert pages == ["@page :first: the repository's templates only"]
    assert any(p.code == "property" and p.detail.startswith("position") for p in found)


def test_a_sheet_the_tokenizer_stops_on_is_refused_whole_with_one_problem():
    found = _check(_css(".a{--z:url(data:\")}.summary::before{content:'Led a team'}.c{--y:\"}"))
    assert [p.code for p in found] == ["property"]


def test_a_construct_at_any_place_inside_a_tag_is_refused_and_between_tags_passes():
    line = '<p class="summary">{{ content.summary }}</p>'
    inside = [*range(1, line.index(">") + 1), *range(line.rindex("<") + 1, len(line))]
    for at in range(len(line) + 1):
        if line.index("{{") < at <= line.index("}}") + 1:
            continue
        files = _cv(SUMMARY, line[:at] + "{% if name %}{% endif %}" + line[at:])
        codes = {problem.code for problem in _check(files)}
        assert ("literal" in codes) == (at in inside), at


def test_whatever_the_sheet_the_check_ends_with_problems_of_its_codes_not_an_error():
    rng = random.Random(427)  # noqa: S311, a repeatable draw of sheets, nothing secret
    pieces = [
        ".x", " ", "{", "}", ";", ":", "color", "#1d232b", "var(", "--a", ")", '"', "'", "\\",
        "/*", "*/", "url(", "@page", "@media", "1em", "-2px", "minmax(", ",", "/", ">", "+",
        "~", "::before", ":root", "!important", "content", "é", "\n", "0", "%", "--rail",
        "rotate(", "45deg", "px-0px", "position", "absolute", "html",
    ]  # fmt: skip
    for _ in range(300):
        sheet = "".join(rng.choice(pieces) for _ in range(rng.randrange(1, 60)))
        found = check_css(sheet, ("nunito-sans",), "#1f4e79")
        assert {problem.code for problem in found} <= set(template_rules.CODES)


# --- round 3: a static filter of allow lists, the browser not modelled (issue #220) -------------

_HEAD_125 = ".headtext { flex: 0 0 125mm; min-width: 0; } .photo { order: 2; }"
LEFT_TO_THE_RENDER_CHECK = [
    # a colour on a background another rule sets: since round 3 the static filter holds only a
    # rule's own pair, and D25b's render check judges the rest; these rows were refused before
    ".x { color: #ffffff; }",
    ".x { color: #fff; }",
    ".x { color: #aaaaaa; }",
    ".summary { background: #1d232b; }",
    ".row { background: #222222; }",
    "html { background: #1d232b; }",
    ".body .summary { color: #f0f0f0; }",
    ".summary:last-child { color: #ffffff; }",
    ".summary:first-child { background: #1d232b; }",
    ".sub + ul { background: #1d232b; }",
    ".nothing-here p { color: #ffffff; }",
    # declared open points: an inline box's vertical padding paints over the line above, a box
    # smaller than its text lets it run under a later positioned box
    "span { padding: 30mm 0; background: #ffffff; }",
    ".summary { width: 1mm; } .row { position: relative; background: #ffffff; }",
    # the review of #613: a box sized by its unwrapped text, or wider than its column or the
    # page, under #575's 125 mm header; the render check's identity shape refuses each
    # (test_design_check_docker.py)
    *(
        f"{_HEAD_125} {contact}"
        for contact in (
            ".contact li { flex: none; }",
            ".contact li { flex-shrink: 0; }",
            ".contact { display: grid; grid-template-columns: max-content; }",
            ".contact { display: grid; grid-template-columns: minmax(max-content, 1fr); }",
            ".contact { display: block; } .contact li { width: 150mm; }",
            ".contact { display: block; } .contact li { min-width: 300mm; }",
        )
    ),
]


@pytest.mark.parametrize("addition", LEFT_TO_THE_RENDER_CHECK)
def test_what_only_a_render_shows_passes_the_static_filter(addition):
    assert _check(_css(addition)) == []


DEEP = "minmax(" * 2000 + "1px" + ")" * 2000
ROUND_THREE_SHEET_REFUSED = [
    # the bundled values' neighbours: no transform at all, contain, punctuation
    (".x::before { transform: scale(0); }", "property"),
    (".x::before { transform: translate(1px, 0); }", "property"),
    (".x::before { transform: rotate(45deg) scale(2); }", "property"),
    (".x::before { transform: rotate(400deg); }", "property"),
    (".x::before { transform: rotate(0.5turn); }", "property"),
    (".x::before { transform: rotate(45); }", "property"),
    (".x { transform: rotate(45deg); }", "property"),
    (".x, .x::before { transform: rotate(45deg); }", "property"),
    (".x::marker { transform: rotate(45deg); }", "property"),
    ('.x::before { content: "•"; transform: rotate(45deg); }', "property"),
    ('.y::after { content: ":"; transform: ROTATE(-90DEG); }', "property"),
    ('.x::before { content: "•"; transform: rotate(0deg); }', "property"),
    ('.x::before { content: "•"; transform: none; }', "property"),
    ('.x::after { content: "•"; transform: rotate(-6deg) !important; }', "property"),
    ('.x::before { content: "•"; TRANSFORM: rotate(1deg); }', "property"),
    (".x { contain: paint; }", "property"),
    (".x { contain: size; }", "property"),
    (".x { contain: strict; }", "property"),
    (".x { contain: inline-size; }", "property"),
    (".x { contain: inline-size layout; }", "property"),
    ('.x::before { content: "#a"; }', "content"),
    ('.x::before { content: "+1"; }', "content"),
    ('.x::before { content: ">>Lead"; }', "content"),
    ('.x { content: "#"; }', "property"),
    # depth and size: a refusal, never an error
    (".summary { width: " + DEEP + "; }", "structure"),
    (".summary { width: " + "(" * 5000 + "; }", "structure"),
    (".x { color: var(var(var(--a))); }", "structure"),
    (".x { grid-template-columns: minmax(minmax(0, 1fr), 1fr); }", "property"),
    # custom properties on :root only: the reviewer's probes, then their neighbours
    (".zz{--b:#000000}.summary{background:var(--b);color:#ffffff}", "property"),
    (".zz{--c:#ffffff}.summary{color:var(--c);background:#000000}", "property"),
    ("body { --x: 1px; }", "property"),
    (":root .x { --x: 1px; }", "property"),
    (":root, .x { --x: 1px; }", "property"),
    (":root:first-child { --x: 1px; }", "property"),
    ("html.x { --x: 1px; }", "property"),
    (":root::before { --x: 1px; }", "property"),
    (":root { --x: 1px; } html { --x: 2px; }", "property"),
    ("@page { --x: 1px; }", "page"),
    # the overlay family: the reviewer's probes, then their neighbours
    (
        ".summary::before{content:'';position:absolute;left:0;top:0;width:300mm;height:300mm;"
        "background:#ffffff}",
        "property",
    ),
    (
        ".row{display:grid}.row > *{grid-row:1;grid-column:1}.label{order:1;background:#ffffff}",
        "property",
    ),
    ('.summary::before{content:"•";color:#000000;background:#000000}', "property"),
    (".x { position: absolute; }", "property"),
    (".x { position: fixed; }", "property"),
    (".x { position: sticky; }", "property"),
    (".x { position: relative; top: 1em; }", "property"),
    (".x { left: 0; }", "property"),
    (".x { inset: 0; }", "property"),
    (".x { z-index: 2; }", "property"),
    (".x { float: left; }", "property"),
    (".x { margin-top: -1pt; }", "property"),
    (".x { margin: 0 -1em; }", "property"),
    (".x { grid-area: 1 / 1; }", "property"),
    (".x { grid-row: 1; }", "property"),
    (".x { grid-row-start: 1; }", "property"),
    (".x { grid-column-start: 1; }", "property"),
    ('.x::before { content: ""; background: #ffffff; }', "property"),
    ('.x::after { content: ""; border: 1px solid #1d232b; }', "property"),
    (".x::before { width: 2mm; }", "property"),
    (".x::before { height: 2mm; }", "property"),
    (".x::before { min-height: 300mm; }", "property"),
    (".x::before { flex: 1 1 auto; }", "property"),
    (".x::before { position: relative; }", "property"),
    (".x::before { background: inherit; }", "property"),
    (".x::marker { background: #000000; }", "property"),
    (".x::marker { padding: 10mm; }", "property"),
    # the pair of one rule: an important declaration wins over a later one
    (".x { color: #ffffff !important; background: #ffffff; color: #1d232b; }", "contrast"),
    # a unit as CSS reads it: `px-0px` is one unit, not a length
    (".x { gap: 1px-0px; }", "property"),
]


@pytest.mark.parametrize(("addition", "code"), ROUND_THREE_SHEET_REFUSED)
def test_round_three_sheet_probes_are_refused(addition, code):
    assert code in {problem.code for problem in _check(_css(addition))}


ROUND_THREE_SHEET_ACCEPTED = [
    '.x::before { content: "#"; } .y::before { content: ">"; } .z::after { content: "+"; }',
    ".x { contain: none; }",
    '.x::before { content: "•"; color: var(--accent); font-weight: 700; padding: 0 6pt; }',
    ".x::marker { color: var(--accent); font-weight: 700; font-family: 'Nunito Sans'; }",
    ".x { list-style: decimal outside; } .y { list-style-type: circle; }",
    "html { --gap: 4pt; } .x { gap: var(--gap); }",
    "html:root { --rail: 20mm; } .x { grid-template-columns: minmax(var(--rail), 1fr) 1fr; }",
    ".x { display: grid; } .x > .a { grid-column: 1 / -1; } .x > .b { grid-column: 2; }",
    ".x { color: #1d232b !important; background: #ffffff; color: #ffffff; }",
    ".x { position: static; } .y { position: relative; }",
]


@pytest.mark.parametrize("addition", ROUND_THREE_SHEET_ACCEPTED)
def test_round_three_sheets_written_another_way_pass(addition):
    assert _check(_css(addition)) == []


@pytest.mark.parametrize(
    ("css", "code", "detail"),
    [
        (".x{color:url(a)}", "property", "url(), a template from an upload embeds nothing"),
        (".x{color:\\72 ed}", "structure", "an escape outside a string"),
        (".x{color:#000000}`", "structure", "a character the rules of a sheet do not use"),
        (".x{width:" + DEEP + "}", "structure", "functions nested deeper than 2"),
        (".x{width:minmax(minmax(minmax(", "structure", "functions nested deeper than 2"),
        ("/*" + "a" * template_css.SHEET_MAX + "*/", "too_large", "a sheet over 64000 characters"),
    ],
)
def test_the_tokenizer_stops_at_what_it_does_not_know(css, code, detail):
    rules, problems = template_css.parse(css, "style.css")
    assert (rules, [(p.code, p.detail) for p in problems]) == ([], [(code, detail)])


def test_a_unit_is_read_as_css_reads_it():
    found = [t for t in template_css.tokens(".x{gap:1px-0px 2PX -1em}") if t.kind == "number"]
    assert [(t.value, t.unit) for t in found] == [(1, "px-0px"), (2, "px"), (-1, "em")]


def test_a_sheet_at_the_cap_is_checked_whole_and_one_over_it_is_refused():
    sheet = FILES["style.css"]
    at_cap = sheet + "/*" + "a" * (template_css.SHEET_MAX - len(sheet) - 4) + "*/"
    assert len(at_cap) == template_css.SHEET_MAX
    assert check_css(at_cap, tuple(MANIFEST["fonts"]), MANIFEST["accent"]) == []
    over = check_css(at_cap + " ", tuple(MANIFEST["fonts"]), MANIFEST["accent"])
    assert [p.code for p in over] == ["too_large"]


ROUND_THREE_RENDER_REFUSED = [
    # the reviewer's probes: the browser would build another tree
    '<div class="dark"><p class="light"><span class="s"><div class="summary">'
    "{{ content.summary }}</div></span></p></div>",
    '<ul class="dark"><li class="light"><span class="s"><li class="summary">'
    "{{ content.summary }}</li></span></li></ul>",
    '<div class="dark"><table class="t"><tr class="r"><td class="summary">{{ content.summary }}'
    "</td></tr></table></div>",
    # their neighbours
    '<p class="a"><strong><em><ul class="b"><li>{{ content.summary }}</li></ul></em></strong></p>',
    '<p class="a"><span><table class="t"><tbody><tr><td>{{ content.summary }}</td></tr></tbody>'
    "</table></span></p>",
    '<p class="a"><span><h2 class="b">{{ content.summary }}</h2></span></p>',
    '<p class="a"><b><hr class="r"></b>{{ content.summary }}</p>',
    '<ul class="a"><li><div class="b"><li>{{ content.summary }}</li></div></li></ul>',
    '<ol class="a"><li><p class="b"><li>{{ content.summary }}</li></p></li></ol>',
    '<div class="a"><li>{{ content.summary }}</li></div>',
    '<table class="t"><tbody><tr><td>{{ content.summary }}</td></tr></tbody><tr><td></td></tr>'
    "</table>",
    '<table class="t">{{ content.summary }}<tbody><tr><td></td></tr></tbody></table>',
]


@pytest.mark.parametrize("markup", ROUND_THREE_RENDER_REFUSED)
def test_round_three_markup_the_browser_would_repair_is_refused(markup):
    files = _cv(SUMMARY, markup)
    assert template_rules.check_html("cv", "cv.html.j2", files["cv.html.j2"]) == []
    assert "structure" in {problem.code for problem in _check(files)}


def test_an_element_closed_by_a_slash_the_browser_keeps_open_is_refused():
    anchor = '<div class="bar"></div>\n\n{% if content.summary or'
    files = _cv(anchor, '<div class="bar"/></div>\n\n{% if content.summary or')
    assert template_rules.check_html("cv", "cv.html.j2", files["cv.html.j2"]) == []
    found = _check(files)
    assert (
        Problem("cv.html.j2", 0, "structure", "a /> on an element the browser keeps open") in found
    )


ROUND_THREE_HTML_REFUSED = [
    # markup an if or a for opens in one branch and closes in another
    (
        '{% if content.summary %}<div class="a">{% endif %}{{ content.summary }}'
        "{% if content.summary %}</div>{% endif %}",
        "structure",
    ),
    (
        '<div class="z">{% if content.headline %}<div class="a">{% else %}<div class="b">'
        "{% endif %}{{ content.summary }}</div></div>",
        "structure",
    ),
    (
        '<div class="a">{% for h in content.highlights %}</div><div class="b">{% endfor %}'
        "{{ content.summary }}</div>",
        "structure",
    ),
    ('<br/><p class="a"/>{{ content.summary }}', "structure"),
    (
        '<table class="t"><tbody><tr><td rowspan="2">{{ content.summary }}</td></tr></tbody>'
        "</table>",
        "attribute",
    ),
    ("{% if name %}" * 13 + SUMMARY + "{% endif %}" * 13, "construct"),
]


@pytest.mark.parametrize(("markup", "code"), ROUND_THREE_HTML_REFUSED)
def test_round_three_markup_probes_are_refused(markup, code):
    assert code in {problem.code for problem in _check(_cv(SUMMARY, markup))}


ROUND_THREE_HTML_ACCEPTED = [
    '<ul class="a"><li><ul class="b"><li>{{ content.summary }}</li></ul></li></ul>',
    '<p class="a"><span class="b"><strong>{{ content.summary }}</strong></span></p>',
    '<table class="t"><tbody><tr><td><div class="c"><p class="d">{{ content.summary }}</p></div>'
    "</td></tr></tbody></table>",
    '<p class="a"><span><br>{{ content.summary }}</span></p>',
    '<table class="t"><thead><tr><th colspan="2">{{ content.summary }}</th></tr></thead></table>',
    '{% if content.summary %}<div class="a">{{ content.summary }}</div>'
    '{% else %}<p class="b"></p>{% endif %}',
    '<h2 class="a"><span>{{ content.summary }}</span></h2>',
    "{% if content.summary %}" * 10 + SUMMARY + "{% endif %}" * 10,
]


@pytest.mark.parametrize("markup", ROUND_THREE_HTML_ACCEPTED)
def test_round_three_markup_written_another_way_passes(markup):
    assert _check(_cv(SUMMARY, markup)) == []


def test_a_private_use_character_is_never_read_as_an_output():
    source = FILES["cv.html.j2"].replace(SUMMARY, SUMMARY + "<p>\ue000</p>")
    expected = Problem("cv.html.j2", 0, "literal", "a character of the private use area")
    assert expected in template_rules.check_html("cv", "cv.html.j2", source)


# --- round 4: no transform at all, no marker string ---------------------------------------------

SQUARES = "▪"
ROUND_FOUR_SHEET_REFUSED = [
    # the reviewer's probes: a turned decoration and an outside marker string paint over the
    # text of other fields
    (
        f".row .body::before{{content:'{SQUARES * 60}';display:block;white-space:nowrap;"
        "font-size:2.5em;font-weight:700;line-height:0.9;letter-spacing:-0.05em;color:#ffffff;"
        "transform:rotate(-6deg)}",
        "transform: it can lay a box over another",
    ),
    (
        f".competencies li{{list-style:outside '{SQUARES * 40}'}}"
        ".competencies li::marker{color:#ffffff;font-size:4em;font-weight:700}",
        "list-style: a value outside the allowed list",
    ),
    # their neighbours, one change each
    ('.x::before { content: "•"; transform: rotate(-6deg); }', "transform: it can lay a box"),
    ('.x::after { content: "•"; transform: rotate(1deg); }', "transform: it can lay a box"),
    (".x { transform: none; }", "transform: it can lay a box"),
    (".x::marker { transform: rotate(45deg); }", "transform: it can lay a box"),
    (f".x {{ list-style: '{SQUARES}' outside; }}", "list-style: a value outside"),
    (f".x {{ list-style: inside '{SQUARES}'; }}", "list-style: a value outside"),
    (".x { list-style: '•'; }", "list-style: a value outside"),
    (".x { list-style-type: '•'; }", "list-style-type: a value outside"),
    (".x { list-style-type: ''; }", "list-style-type: a value outside"),
    ('.x::marker { content: "•"; }', "content: a ::marker takes only a colour and a font"),
    ('.x::marker { content: ""; }', "content: a ::marker takes only a colour and a font"),
    (".x::marker { content: none; }", "content: a ::marker takes only a colour and a font"),
    (".x::marker { font-size: 4em; }", "font-size: a ::marker takes only a colour and a font"),
    (".x::marker { font-size: 100%; }", "font-size: a ::marker takes only a colour and a font"),
    (".x li::marker { letter-spacing: 0.5em; }", "letter-spacing: a ::marker takes only"),
]


@pytest.mark.parametrize(("addition", "detail"), ROUND_FOUR_SHEET_REFUSED)
def test_round_four_sheet_probes_are_refused(addition, detail):
    found = _check(_css(addition))
    assert any(p.code == "property" and p.detail.startswith(detail) for p in found), found


ROUND_FOUR_SHEET_ACCEPTED = [
    ".x { list-style: square outside; } .y { list-style: inside; } .z { list-style: none; }",
    ".x { list-style-type: decimal; list-style-position: outside; }",
    ".x li::marker { color: #6b6b6b; font-style: italic; font-variant: small-caps; }",
    '.x::before { content: "•"; color: var(--accent); margin-right: 4pt; }',
]


@pytest.mark.parametrize("addition", ROUND_FOUR_SHEET_ACCEPTED)
def test_round_four_sheets_written_another_way_pass(addition):
    assert _check(_css(addition)) == []


# --- PR #518 round 2: the page box and the shape of the content (issue #220) ------------------

GEOMETRY_REFUSED = [
    # the reviewer's page: a margin of its own, which print uses and the check does not
    ("@page { margin: 10mm 10mm 10mm 120mm; } .bar { margin-top: var(--margin); }", "page"),
    ("@page { margin: 0; }", "page"),
    ("@page { margin: 15mm; }", "page"),
    ("@page { margin: var(--margin) 120mm; }", "page"),
    ("@page { margin: var(--section-gap); }", "page"),
    ("@page { margin-left: 120mm; }", "page"),
    ("@page { margin-left: var(--margin); }", "page"),
    ("@page { margin-top: 0; }", "page"),
    ("@page { padding: 0; }", "page"),
    ("@page { width: 100mm; }", "page"),
    ("@page { size: A5; }", "page"),
    ("@page { size: A4 landscape; }", "page"),
    ("@page { size: 210mm 297mm; }", "page"),
    ("@page { size: A4; @top-center { content: ','; } }", "structure"),
    ("@page :first { margin: 0; }", None),
    ("@page :left { margin: var(--margin); }", None),
    # the reviewer's box: a fixed height its text outgrows; then the family
    (".summary { height: 3.2em; }", "property"),
    (".summary { height: 0; }", "property"),
    (".summary { height: 30mm; }", "property"),
    (".summary { height: 100%; }", "property"),
    (".summary { height: var(--section-gap); }", "property"),
    (".summary { max-height: 3em; }", "property"),
    (".photo .summary { height: 1em; }", "property"),
    (".photo::before { height: 1em; }", "property"),
    ("img, .summary { height: 25mm; }", "property"),
    (".photos { height: 25mm; }", "property"),
    ("p.photo { height: 1em; }", "property"),
    # the reviewer's lone item grid, then the family: fixed or empty tracks
    (
        ".schools .school:first-child:last-child { display: grid;"
        " grid-template-columns: 0px 0px; grid-auto-flow: column; }",
        "property",
    ),
    (
        ".languages li:first-child:last-child { display: grid; grid-template-columns: 0 0; }",
        "property",
    ),
    (".x { grid-template-columns: 1px 1fr; }", "property"),
    (".x { grid-template-columns: 19mm 1fr; }", "property"),
    (".x { grid-template-columns: 4em 1fr; }", "property"),
    (".x { grid-template-columns: 5% 1fr; }", "property"),
    (".x { grid-template-columns: 0fr 0fr; }", "property"),
    (".x { grid-template-columns: minmax(0, 0px) 1fr; }", "property"),
    (".x { grid-template-columns: minmax(0, 0fr) 1fr; }", "property"),
    (".x { grid-template-columns: minmax(1fr, 1fr) 1fr; }", "property"),
    (".x { grid-template-columns: var(--item-gap) 1fr; }", "property"),
    (":root { --rail: 2mm; } .x { grid-template-columns: var(--rail) 1fr; }", "property"),
    (".x { grid-template-rows: 1px 1px 1px; }", "property"),
    (".x { grid-template-rows: 3em; }", "property"),
    (".x { grid-template-rows: 30mm 1fr; }", "property"),
    (".x { grid-template-rows: 50%; }", "property"),
    (".x { grid-template-rows: minmax(0, 1fr); }", "property"),
    (".x { grid-auto-rows: 0; }", "property"),
    (".x { grid-auto-rows: 2em; }", "property"),
    (".x { grid-auto-columns: 0; }", "property"),
]


@pytest.mark.parametrize(("addition", "code"), GEOMETRY_REFUSED)
def test_what_sets_the_page_box_or_a_box_shorter_than_its_text_is_refused(addition, code):
    found = _check(_css(addition))
    assert found
    if code is not None:
        assert code in {p.code for p in found}, found


GEOMETRY_ACCEPTED = [
    "@page { size: A4; }",
    "@page { size: A4; margin: var(--margin); }",
    "@page { size: a4 portrait; margin: var(--margin) !important; }",
    ".photo { height: 25mm; width: 20mm; }",
    "img { height: 30mm; }",
    ".header .photo { height: 24mm; }",
    "img.photo, .side .photo { height: 26mm; }",
    ".summary { height: auto; min-height: auto; }",
    ".photo { min-height: 25mm; }",
    ".x { grid-template-columns: 30mm 1fr; }",
    ".x { grid-template-columns: 20mm minmax(0, 1fr); }",
    ".x { grid-template-columns: minmax(0, 1fr) 62mm; }",
    ".x { grid-template-columns: 1fr 1fr 1fr; }",
    ".x { grid-template-columns: auto 1fr; }",
    ".x { grid-template-columns: max-content minmax(min-content, 1fr); }",
    ".x { grid-template-columns: 25% 75%; }",
    ".x { grid-template-columns: 6em 1fr; }",
    ".x { grid-template-columns: minmax(10mm, 30mm) 1fr; }",
    ":root { --rail: 27mm; } .x { grid-template-columns: var(--rail) minmax(0, 1fr); }",
    ".x { grid-template-rows: auto 1fr; grid-auto-rows: min-content; }",
    ".x { grid-template-rows: none; }",
]


@pytest.mark.parametrize("addition", GEOMETRY_ACCEPTED)
def test_a_page_box_of_the_step_and_boxes_that_grow_with_their_text_pass(addition):
    assert _check(_css(addition)) == []


# PR #518 round 3: the app's own rules come after the sheet (template_css.ENFORCED_CSS), so a
# sheet may not mark the properties they set `!important`; a column flex item keeps its
# automatic minimum height (min-height only `auto` off the photo), so no flex basis makes it
# shorter than its text; the class `photo`, which may hold a height, is the portrait's alone.
ENFORCED_REFUSED = [
    "li, p { overflow-wrap: normal !important; }",
    ".contact li { overflow-wrap: normal ! important; }",
    ".x { overflow-wrap: break-word !IMPORTANT; }",
    ".x { word-break: keep-all !important; }",
    ".x { overflow-wrap: inherit !important; }",
    ".x::before { content: ','; overflow-wrap: normal !important; }",
    # the reviewer's flex basis as a height, then the family
    ".body { display: flex; flex-direction: column; } .summary { flex: 0 0 34mm; min-height: 0; }",
    ".summary { flex-basis: 30mm; min-height: 0; }",
    ".summary { min-height: 0; }",
    ".summary { min-height: 5mm; }",
    ".summary { min-height: 1em; }",
    ".summary { min-height: var(--section-gap); }",
    ".photo .summary { min-height: 0; }",
    "p.photo { min-height: 0; }",
]


@pytest.mark.parametrize("addition", ENFORCED_REFUSED)
def test_a_sheet_that_would_outrank_the_apps_rules_or_shrink_a_flex_item_is_refused(addition):
    assert "property" in {p.code for p in _check(_css(addition))}


ENFORCED_ACCEPTED = [
    "li, p { overflow-wrap: break-word; }",
    ".x { word-break: keep-all; overflow-wrap: normal; }",
    ".summary { color: #1d232b !important; }",
    ".body { display: flex; flex-direction: column; } .summary { flex: 0 0 34mm; }",
    ".summary { min-height: auto; }",
    "img { min-height: 20mm; }",
]


@pytest.mark.parametrize("addition", ENFORCED_ACCEPTED)
def test_a_sheet_the_apps_rules_still_end_passes(addition):
    assert _check(_css(addition)) == []


# Issue #575: nowrap beats overflow-wrap even when the app's declaration is important.
HEADER_125_SHEET = (
    ".headtext { flex: 0 0 125mm; } .photo { order: 2; }"
    " .contact { display: block; } .contact li { white-space: nowrap; }"
)
DATA_SELECTORS = [
    "html", "body", ".headtext", ".contact", ".contact li", ".contact a",
    ".contact li:first-child", ".headtext, .contact a",
]  # fmt: skip


def test_the_reviewers_125mm_nowrap_header_is_refused():
    found = _check(_css(HEADER_125_SHEET))
    assert [(p.code, p.detail) for p in found] == [
        ("property", "white-space: a value outside the allowed list")
    ]


@pytest.mark.parametrize("selector", DATA_SELECTORS)
@pytest.mark.parametrize(
    ("prop", "value"),
    [
        ("white-space", "nowrap"),
        ("WHITE-SPACE", "NOWRAP"),
        ("white-space", "/* before */ nowrap /* after */"),
        ("white-space", "nowrap ! /* priority */ IMPORTANT"),
        ("contain", "inline-size"),
        ("CONTAIN", "INLINE-SIZE"),
        ("contain", "/* before */ inline-size /* after */"),
        ("contain", "inline-size ! /* priority */ IMPORTANT"),
    ],
)
def test_captured_data_cannot_disable_wrapping_or_inline_sizing(selector, prop, value):
    addition = f"{selector} {{ {prop}: {value}; }} .contact a {{ {prop}: inherit; }}"
    assert [(p.code, p.detail) for p in _check(_css(addition))] == [
        ("property", f"{prop.lower()}: a value outside the allowed list")
    ]


@pytest.mark.parametrize("selector", DATA_SELECTORS)
@pytest.mark.parametrize("value", ["normal", "pre-line", "pre-wrap", "inherit"])
def test_wrapping_values_and_uncontained_boxes_still_pass(selector, value):
    addition = (
        f"{selector} {{ white-space: {value}; contain: none; }}"
        " .contact a { white-space: inherit; contain: inherit; }"
        " .contact { flex-wrap: nowrap; }"
    )
    assert _check(_css(addition)) == []


@pytest.mark.parametrize("value", ["normal", "pre-line", "pre-wrap"])
def test_the_reviewers_header_passes_when_its_contact_text_may_wrap(value):
    sheet = HEADER_125_SHEET.replace("white-space: nowrap", f"white-space: {value}")
    assert _check(_css(sheet)) == []


PHOTO_CLASS_REFUSED = [
    # the class `photo` on a text, through a macro's class or on the markup
    (_cv("{{ base.header(content.headline) }}", "{{ base.header(content.headline) }}"
         "{{ base.contact_lines(\"photo\") }}")),
    _cv(SUMMARY, '<p class="summary photo">{{ content.summary }}</p>'),
    _cv(SUMMARY, '<div class="photo"><p class="summary">{{ content.summary }}</p></div>'),
    _cv(SUMMARY, '<p class="photo">{{ content.summary }}</p>'),
    _cv(ROLES, "{% for role in roles %}\n{{ base.employer(role) }}\n{% endfor %}"
        "{{ base.bullets(content.highlights, \"photo\") }}"),
]  # fmt: skip


@pytest.mark.parametrize("files", PHOTO_CLASS_REFUSED)
def test_the_class_photo_is_the_portraits_alone(files):
    detail = "the class photo on another element than the portrait"
    assert Problem("cv.html.j2", 0, "attribute", detail) in _check(files)


def test_the_portrait_may_take_the_class_photo_among_others():
    header = "{{ base.header(content.headline) }}"
    files = _cv(header, header.replace("header(", 'portrait("photo round") }}{{ base.header('))
    assert not any(p.detail.startswith("the class photo") for p in _check(files))


def test_the_apps_rules_come_last_in_every_built_page():
    sample = design_check.canary("cv")
    for template in templates.registry().values():
        page = templates.build_html(
            template, "cv", sample.content, sample.view, "en", 0, sample.rendered_on
        )
        assert page.split("</style>", 1)[0].endswith(template_css.ENFORCED_CSS)
