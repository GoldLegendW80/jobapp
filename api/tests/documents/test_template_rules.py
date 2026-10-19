"""template_rules (task D25a): the checks of a template the design model writes.

The true inputs are the bundled templates, each of which must pass untouched. The refusals are
classic's files with one change each, from the spec's step 4 and its neighbours: a script, an
outside address, `@import`, a literal sentence in every place a template can hold one (HTML, a
Jinja constant, a CSS `content`, an escape), a missing field and a field printed only under
another test, a CSS that ignores or pins the density steps, and what would hide a field.
"""

import json
import secrets

import pytest
from jinja2 import Environment, nodes

from app.documents import content as c
from app.documents import template_rules, templates
from app.documents.template_rules import FIELDS, LABELS, MACROS, Problem, check

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


@pytest.mark.parametrize("folder", list(_bundled()), ids=lambda f: f.name)
def test_every_bundled_template_passes_untouched(folder):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    files = {name: (folder / name).read_text(encoding="utf-8") for name in templates.FILES}
    found = check(
        files,
        photo=manifest["photo"],
        fonts=tuple(manifest["fonts"]),
        accent=manifest["accent"],
    )
    assert found == []


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
        "{{ group.label }}{% for i in group.items %}{{ i }}"
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
    (_css("@page { margin: 0; }"), "density_unread"),
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
    (_css(".x { color: #ffffff; }"), "contrast"),
    (_css(".x { color: #fff; }"), "contrast"),
    (_css(".x { color: #aaaaaa; }"), "contrast"),
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
    _css('.x::before { content: "\\00b7\\00a0"; } .y::after { content: "•"; } .z { content: ""; }'),
    _css(".band { background: #1f4e79; } .band h2 { color: #ffffff; }"),
    _css(".band { background: var(--accent); } .band p { color: #dff3ef; }"),
    _css("@page { size: A4; margin: 0; } .page { padding: var(--margin); }"),
    _css(".x { color: var(--accent); font-size: 115%; line-height: 1.2; letter-spacing: 0.1em; }"),
    _css(".x { font-family: 'Nunito Sans', sans-serif; } .y { font-weight: 700 !important; }"),
    _css(":root { --rail: 27mm; } .x { grid-template-columns: var(--rail) minmax(0, 1fr); }"),
    _css("/* a comment with { braces } and ; */ .x { color: #1d232b; }"),
    _css(".x { position: absolute; left: 0; top: 0.2em; }"),
    _css('.x { list-style: "• " inside; } .y { list-style-type: none; }'),
]


@pytest.mark.parametrize("files", CSS_ACCEPTED)
def test_a_style_sheet_written_another_way_passes(files):
    assert _check(files) == []


def test_the_accent_is_held_to_the_same_contrast_as_any_text():
    assert {p.code for p in _check(accent="#f0f0f0")} == {"contrast"}
    assert _check(accent="#1F4E79") == []


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
    assert len(found) >= 8
    assert not [p for p in found if marker in str(p) or marker in p.code]
    assert {p.code for p in found} <= set(template_rules.CODES)
