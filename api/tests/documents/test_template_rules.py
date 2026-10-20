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
    assert template_rules.check_html("cv", "cv.html.j2", files["cv.html.j2"]) == []
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
    _cv(SUMMARY, '<table class="t"><tr><td>{{ content.summary }}</td></tr></table>'),
    _top("\n\n{# a comment #}\n"),
]


@pytest.mark.parametrize("files", ROUND_TWO_ACCEPTED)
def test_round_two_true_inputs_pass(files):
    assert _check(files) == []


SHEET_REFUSED = [
    # the reviewer's probes: a url( and a comment the browser reads otherwise
    ".a{--z:url(data:\")}.summary::before{content:'Led a team of forty'}.c{--y:\"}",
    '.a url(data:"){}.summary{display:none}.c{--y:"}',
    ".a{--z:url(data:/*)}.summary::before{content:'Led a team'}.c{--y:*/}",
    '.a{--z:URL(data:")}.summary::before{content:"Lead"}.c{--y:"}',
    ".a{--z:Url(data:x)}",
    ".a{--z:url( data:x )}",
    '.summary{background:url("data:image/png;base64,AAAA")}',
    # the reviewer's probes through var()
    ':root{--x:"Led a team of forty "}.summary{display:list-item;list-style-type:var(--x)}',
    ":root{--n:-9999px}.summary{position:absolute;left:var(--n)}",
    ':root{--x:image-set("\\68ttps\\3a\\2f\\2f example.org\\2f a.png" 1x)}'
    ".summary{display:list-item;list-style:var(--x)}",
    ":root{--x:none}.summary{display:var(--x)}",
    ":root{--x:#1d232b}:root{--x:#ffffff}.summary{color:var(--x)}",
    ".summary{font-size:var(--Body-Size)}",
    ".summary{font-size:var(--body-size, 9pt)}",
    ".summary{color:var(--undefined)}",
    ":root{--rail:9999mm}",
    # the reviewer's contrast probes and the cascade
    ".summary { color: #1d232b; background: #1d232b; }",
    ".summary{color:var(--accent);background:var(--accent)}",
    ".summary { background: #1d232b; }",
    ".row { background: #222222; }",
    "html { background: #1d232b; }",
    ".body .summary { color: #f0f0f0; }",
    ".summary:last-child { color: #ffffff; }",
    ".summary:first-child { background: #1d232b; }",
    ".sub + ul { background: #1d232b; }",
    ".nothing-here p { color: #ffffff; }",
    # the follow ups: hidden by offsets, a generic family first, an at-rule that looks like @page
    ".summary { margin-left: -1000em; }",
    ".summary { position: relative; left: 9999em; }",
    ".summary { width: 5000mm; }",
    ".summary { font-family: serif; }",
    '.summary { font-family: sans-serif, "Nunito Sans"; }',
    "@pagex { size: A4; }",
    # selectors outside the allow list
    "[title] { color: #000000; }",
    ".x:not(.y) { color: #000000; }",
    ".x:hover { color: #000000; }",
    "#id { color: #000000; }",
    "svg { color: #000000; }",
    ".x::first-line { color: #000000; }",
    ".a .b .c .d .e { color: #000000; }",
    ".x, { color: #000000; }",
    # input the tokenizer stops on
    ".x { color: #１２３４５６; }",
    ".é { color: #000000; }",
    ".x { col\\or: #000000; }",
    ".x { color: #000000; } /* not closed",
    '.x::before { content: "•; }',
    '.x::before { content: "a\\\nb"; }',
    # values outside the allow lists
    ".x { display: contents; }",
    ".x::before { content: counter(x); }",
    ".x { list-style-type: disclosure-open; }",
    ".x { grid-template-columns: repeat(2, 1fr); }",
    ".x { columns: 2 200mm; }",
    ".x { margin: 1fr; }",
    ".x { color: #000000 !ie; }",
    ".x { width: minmax(0, 1fr); }",
    ".x { border: 1px solid red; }",
]


@pytest.mark.parametrize("addition", SHEET_REFUSED)
def test_round_two_sheet_probes_are_refused(addition):
    assert _check(_css(addition))


SHEET_ACCEPTED = [
    '.x::before { content: "\\00B7"; }',
    ".a > .b + .c ~ .d { color: #1d232b; }",
    ".x { border: 1px solid #1d232b; } .y { border-bottom: 0; }",
    "@PAGE { size: A4; }",
    ".x { grid-template-columns: minmax(0, 1fr) 58mm; grid-column: 1 / -1; }",
    ".x:last-child { color: #1d232b; }",
    ".summary { background: #1f4e79; color: #ffffff; }",
]


@pytest.mark.parametrize("addition", SHEET_ACCEPTED)
def test_round_two_sheets_written_another_way_pass(addition):
    assert _check(_css(addition)) == []


def test_a_sheet_the_tokenizer_stops_on_is_refused_whole_with_one_problem():
    found = _check(_css(".a{--z:url(data:\")}.summary::before{content:'Led a team'}.c{--y:\"}"))
    assert [p.code for p in found] == ["property"]


def test_the_contrast_of_many_rules_is_linear_in_their_number():
    backs = "".join(f".b{i}{{background:#f{i % 10}f{i % 10}f{i % 10}}}" for i in range(1300))
    colours = "".join(f".c{i}{{color:#1{i % 10}1{i % 10}1{i % 10}}}" for i in range(1300))
    sheet = FILES["style.css"] + backs + colours
    assert len(sheet) <= template_rules.FILE_MAX
    assert not {p.code for p in _check({**FILES, "style.css": sheet})} - {"contrast"}
