"""D1 spike, throwaway (deleted by D3): the fixtures of api/tests/documents/fixtures/content/ as HTML.

    python make_html.py <fixtures dir> <out dir>

Runs in the d1spike-weasy image (it needs fontTools and Pillow, which WeasyPrint brings). Writes
<out>/html/: every fixture in two layouts (`one`: one column, no photo; `two`: a sidebar with a
generated placeholder photo) at the eight density steps of a fit loop (s0 loosest, s7 tightest),
the edge cases (`edge-*`) and one page per CSS feature (`probe-*`), and <out>/html/index.json.

Fonts: the fixtures name DejaVu Sans and DejaVu Serif (fonts-dejavu-core, free), installed in
both images with the fontconfig cache built at image build. WeasyPrint writes every @font-face to
a temporary file, which the parser sandbox refuses (read only root, no file growth), so @font-face
from data: is only an edge case (`edge-data-font`) and one probe: DejaVu subset to Latin, renamed
`TplSans` and `TplSerif`. The check of a missing font is engine agnostic: compare.py compares the
fonts each PDF embeds with the families the HTML asks for.
"""

import base64
import html
import io
import json
import sys
from pathlib import Path

DEJAVU = Path("/usr/share/fonts/truetype/dejavu")
FACES = (  # (css family, weight, file, PostScript name given to the subset)
    ("Tpl Sans", 400, "DejaVuSans.ttf", "TplSans-Regular"),
    ("Tpl Sans", 700, "DejaVuSans-Bold.ttf", "TplSans-Bold"),
    ("Tpl Serif", 400, "DejaVuSerif.ttf", "TplSerif-Regular"),
)
LATIN = (
    list(range(0x20, 0x7F))
    + list(range(0xA0, 0x180))
    + [0x2013, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2026, 0x20AC]
)
STEPS = 8
MONTHS = {
    "en": [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ],
    "fr": [
        "janv.",
        "févr.",
        "mars",
        "avr.",
        "mai",
        "juin",
        "juil.",
        "août",
        "sept.",
        "oct.",
        "nov.",
        "déc.",
    ],
}
LABELS = {
    "en": {
        "summary": "Profile",
        "highlights": "Highlights",
        "competencies": "Competencies",
        "experience": "Experience",
        "skills": "Skills",
        "education": "Education",
        "languages": "Languages",
        "contact": "Contact",
        "present": "present",
        "to": "to",
        "date": "14 October 2026",
    },
    "fr": {
        "summary": "Profil",
        "highlights": "Points forts",
        "competencies": "Compétences",
        "experience": "Expérience",
        "skills": "Outils",
        "education": "Formation",
        "languages": "Langues",
        "contact": "Contact",
        "present": "aujourd'hui",
        "to": "à",
        "date": "14 octobre 2026",
    },
}
E = html.escape


def subset_fonts() -> dict[str, str]:
    """PostScript name -> woff2 bytes as a data: URI."""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    faces = {}
    for family, weight, file, ps in FACES:
        font = TTFont(DEJAVU / file)
        sub = subset.Subsetter(subset.Options())
        sub.populate(unicodes=LATIN)
        sub.subset(font)
        for record in font["name"].names:
            if record.nameID in (1, 4, 6, 16):
                record.string = {1: family, 16: family, 4: ps.replace("-", " "), 6: ps}[
                    record.nameID
                ]
        font.flavor = "woff2"
        buf = io.BytesIO()
        font.save(buf)
        faces[ps] = (
            "data:font/woff2;base64," + base64.b64encode(buf.getvalue()).decode()
        )
    return faces


def font_css(faces: dict[str, str]) -> str:
    return "".join(
        f'@font-face{{font-family:"{family}";font-weight:{weight};src:url("{faces[ps]}") format("woff2")}}\n'
        for family, weight, _, ps in FACES
    )


def placeholder_photo() -> str:
    """A generated head and shoulders silhouette, JPEG, as a data: URI. Nobody's photo."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (360, 360), (196, 206, 218))
    draw = ImageDraw.Draw(img)
    draw.ellipse((120, 60, 240, 180), fill=(120, 134, 152))
    draw.ellipse((60, 200, 300, 440), fill=(120, 134, 152))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def density(step: int) -> dict[str, float]:
    return {
        "fs": 10.5 - 0.25 * step,
        "lh": 1.42 - 0.03 * step,
        "gap": 11 - 1.2 * step,
        "m": 16 - step,
    }


def period(start: str | None, end: str | None, lang: str) -> str:
    def one(value: str | None) -> str:
        if not value:
            return LABELS[lang]["present"]
        year, _, month = value.partition("-")
        return f"{MONTHS[lang][int(month) - 1]} {year}" if month else year

    return f"{one(start)} {LABELS[lang]['to']} {one(end)}"


BASE_CSS = """
:root{--fs:%(fs).2fpt;--lh:%(lh).2f;--gap:%(gap).1fpt;--accent:#1f4e79;--muted:#5a6270;--tint:#eef3f8}
html{font-family:"Tpl Sans";font-size:var(--fs);line-height:var(--lh);color:#1d232b}
body{margin:0}
h1{font-family:"Tpl Serif";font-weight:400;font-size:calc(var(--fs) * 2.3);line-height:1.1;margin:0}
h2{font-size:calc(var(--fs) * 1.05);text-transform:uppercase;letter-spacing:.08em;color:var(--accent);
   border-bottom:1px solid #c9d3df;margin:0 0 calc(var(--gap) / 3);padding-bottom:1pt}
section{margin-top:var(--gap)}
p{margin:0 0 calc(var(--gap) / 2)}
ul{margin:0;padding-left:1.2em}
li{overflow-wrap:anywhere}
li::marker{color:var(--accent)}
.headline{color:var(--accent);font-weight:700;margin-top:2pt}
.chips{display:flex;flex-wrap:wrap;gap:3pt 4pt}
.chips span{border:1px solid var(--accent);border-radius:8pt;padding:0 5pt;background:var(--tint)}
.job{margin-bottom:calc(var(--gap) / 2)}
.job-head{display:flex;justify-content:space-between;align-items:baseline;gap:8pt}
.job-org{font-weight:700}
.job-when{color:var(--muted);white-space:nowrap}
.skills{display:grid;grid-template-columns:auto 1fr;gap:2pt 8pt}
.skills b{color:var(--accent)}
.muted{color:var(--muted)}
"""
ONE_CSS = """
@page{size:A4;margin:%(m)dmm}
header{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--accent);padding-bottom:4pt}
.contacts{text-align:right;color:var(--muted)}
.cols2{columns:2;column-gap:14pt}
"""
TWO_CSS = """
@page{size:A4;margin:0}
.page{display:grid;grid-template-columns:62mm 1fr;min-height:297mm}
aside{background:var(--accent);color:#fff;padding:%(m)dmm 7mm}
aside h2{color:#fff;border-bottom-color:rgba(255,255,255,.45)}
aside .muted{color:#d6e2ef}
.photo{width:34mm;height:34mm;border-radius:50%%;object-fit:cover;display:block;margin:0 auto 8pt;border:2pt solid #fff}
main{padding:%(m)dmm 12mm %(m)dmm 9mm}
.chips span{background:#fff}
.cols2{columns:2;column-gap:10pt;column-rule:1px solid #c9d3df}
.skills-side b{display:block;color:#fff;margin-top:3pt}
"""


def contacts(identity: dict) -> list[str]:
    return [
        identity["email"],
        identity["phone"],
        identity["location"],
        *identity["links"],
    ]


def cv_sections(c: dict, profile: dict, lang: str, layout: str) -> tuple[str, str]:
    """(main column, sidebar) of a CV; one column puts the sidebar's parts in the main flow."""
    L, fixed = LABELS[lang], profile["fixed"]
    employers = {e["key"]: e for e in fixed["employers"]}
    main = [f"<section><h2>{L['summary']}</h2><p>{E(c['summary'])}</p></section>"]
    if c.get("highlights"):
        main.append(
            f"<section><h2>{L['highlights']}</h2><ul>"
            + "".join(f"<li>{E(h)}</li>" for h in c["highlights"])
            + "</ul></section>"
        )
    if c.get("competencies"):
        main.append(
            f"<section><h2>{L['competencies']}</h2><div class=chips>"
            + "".join(f"<span>{E(x)}</span>" for x in c["competencies"])
            + "</div></section>"
        )
    jobs = []
    for entry in c["experience"]:
        e = employers[entry["employer_key"]]
        jobs.append(
            f"<div class=job><div class=job-head><span><span class=job-org>{E(e['organisation'])}</span>, {E(e['title'])}</span>"
            f"<span class=job-when>{E(period(e['start'], e['end'], lang))}</span></div>"
            "<ul>"
            + "".join(f"<li>{E(b)}</li>" for b in entry["bullets"])
            + "</ul></div>"
        )
    main.append(f"<section><h2>{L['experience']}</h2>{''.join(jobs)}</section>")
    skills = (
        f"<section><h2>{L['skills']}</h2><div class=skills>"
        + "".join(
            f"<b>{E(s['label'])}</b><span>{E(', '.join(s['items']))}</span>"
            for s in c["skills"]
        )
        + "</div></section>"
    )
    schools = (
        f"<section><h2>{L['education']}</h2>"
        + "".join(
            f"<div><b>{E(s['degree'])}</b><br><span class=muted>{E(s['name'])}, {E(s['start'])} {L['to']} {E(s['end'])}</span></div>"
            for s in fixed["schools"]
        )
        + "</section>"
    )
    langs = (
        f"<section><h2>{L['languages']}</h2>"
        + "".join(
            f"<div>{E(x['name'])} <span class=muted>({E(x['level'])})</span></div>"
            for x in fixed["languages"]
        )
        + "</section>"
    )
    if layout == "one":
        main += [skills, f"<div class=cols2>{schools}{langs}</div>"]
        return "".join(main), ""
    return "".join(main), skills.replace(
        "class=skills", "class=skills-side"
    ) + schools + langs


def letter_sections(c: dict, lang: str) -> str:
    L = LABELS[lang]
    return (
        f"<p class=muted style='margin-top:var(--gap)'>{E(c['recipient'])}<br>{L['date']}</p>"
        f"<p><b>{E(c['subject'])}</b></p><p>{E(c['salutation'])}</p>"
        + "".join(f"<p style='text-align:justify'>{E(p)}</p>" for p in c["paragraphs"])
        + f"<p>{E(c['closing'])}</p>"
    )


def installed(text: str) -> str:
    return text.replace("Tpl Sans", "DejaVu Sans").replace("Tpl Serif", "DejaVu Serif")


def document(
    doc: dict, profile: dict, layout: str, step: int, fonts: str, photo: str
) -> str:
    c, lang, ident = doc["content"], doc["language"], profile["identity"]
    d = density(step)
    css = (
        font_css_cache[fonts]
        + BASE_CSS % d
        + (ONE_CSS if layout == "one" else TWO_CSS) % d
    )
    if doc["kind"] == "cv":
        main, side = cv_sections(c, profile, lang, layout)
    else:
        main, side = letter_sections(c, lang), ""
    head = f"<h1>{E(ident['name'])}</h1><div class=headline>{E(c['headline'])}</div>"
    if layout == "one":
        contact = (
            "<div class=contacts>"
            + "<br>".join(E(x) for x in contacts(ident))
            + "</div>"
        )
        body = f"<header><div>{head}</div>{contact}</header>{main}"
    else:
        contact = (
            f"<section><h2>{LABELS[lang]['contact']}</h2>"
            + "".join(
                f"<div style='overflow-wrap:anywhere'>{E(x)}</div>"
                for x in contacts(ident)
            )
            + "</section>"
        )
        body = f"<div class=page><aside><img class=photo src='{photo}' alt=''>{contact}{side}</aside><main>{head}{main}</main></div>"
    page = f"<!doctype html><html lang={lang}><head><meta charset=utf-8><style>{css}</style></head><body>{body}</body></html>"
    return page if fonts == "data" else installed(page)


# One page per CSS feature: the same markup for both engines, judged side by side (compare.py).
PROBE_PAGE = "@page{size:120mm 70mm;margin:6mm}html{font-family:'Tpl Sans';font-size:10pt}body{margin:0}"
BOX = "background:#cfe0f1;border:1px solid #1f4e79;padding:2pt"
PROBES = {
    "flex": (
        "",
        f"<div style='display:flex;justify-content:space-between;align-items:center;height:20mm;{BOX}'><span style='{BOX}'>A</span><span style='{BOX};height:12mm'>B</span><span style='{BOX}'>C</span></div>",
    ),
    "flex-wrap-gap": (
        "",
        "<div style='display:flex;flex-wrap:wrap;gap:4pt 8pt;width:70mm'>"
        + "".join(
            f"<span style='{BOX};border-radius:6pt'>chip {i}</span>" for i in range(9)
        )
        + "</div>",
    ),
    "flex-column-gap": (
        "",
        f"<div style='display:flex;flex-direction:column;gap:6pt;width:40mm'><div style='{BOX}'>1</div><div style='{BOX}'>2</div><div style='{BOX}'>3</div></div>",
    ),
    "inline-flex": (
        "",
        f"text <span style='display:inline-flex;gap:3pt;{BOX}'><b>x</b><b>y</b></span> after",
    ),
    "grid-fr": (
        "",
        "<div style='display:grid;grid-template-columns:1fr 2fr 1fr;gap:4pt'>"
        + "".join(f"<div style='{BOX}'>{i}</div>" for i in range(6))
        + "</div>",
    ),
    "grid-auto-1fr": (
        "",
        "<div style='display:grid;grid-template-columns:auto 1fr;gap:2pt 8pt'><b>Languages</b><span>Python, Go</span><b>Data</b><span>PostgreSQL, a much longer value that wraps onto a second line here</span></div>",
    ),
    "grid-areas": (
        ".g{display:grid;grid-template-areas:'h h' 's m';grid-template-columns:30mm 1fr;gap:4pt}",
        f"<div class=g><div style='grid-area:h;{BOX}'>header</div><div style='grid-area:s;{BOX}'>side</div><div style='grid-area:m;{BOX}'>main</div></div>",
    ),
    "custom-properties-calc": (
        ":root{--a:#b0413e;--w:30mm}.c{width:calc(var(--w) + 10mm);background:var(--a);color:#fff;padding:2pt}",
        "<div class=c>calc(var(--w) + 10mm)</div>",
    ),
    "min-max-clamp": (
        "",
        f"<div style='width:min(40mm, 90%);{BOX}'>min</div><div style='width:max(20mm, 30%);{BOX}'>max</div><div style='width:clamp(10mm, 50%, 45mm);{BOX}'>clamp</div>",
    ),
    "font-face-data": (
        "",
        "<div style=\"font-family:'Tpl Serif';font-size:16pt\">Serif from data: Éé àç</div><div><b>Bold sans</b> regular sans</div>",
    ),
    "font-weight-600": (
        "",
        "<span style='font-weight:600'>600 (only 400 and 700 exist)</span> <span style='font-weight:300'>300</span>",
    ),
    "page-margin-box": (
        "@page{@bottom-right{content:'page ' counter(page);font-size:8pt;color:#b0413e}}",
        "Footer box at the bottom right?",
    ),
    "multi-column": (
        "",
        "<div style='columns:2;column-gap:8pt;column-rule:1px solid #1f4e79'>"
        + "word " * 60
        + "</div>",
    ),
    "borders-radius": (
        "",
        "<div style='display:flex;gap:6pt'>"
        + "".join(
            f"<div style='width:18mm;height:12mm;border:2pt {s} #1f4e79;border-radius:{r}'></div>"
            for s, r in (
                ("solid", "0"),
                ("dashed", "4pt"),
                ("double", "8pt"),
                ("dotted", "50%"),
            )
        )
        + "</div>",
    ),
    "background-colour-gradient": (
        "",
        "<div style='height:10mm;background:#1f4e79'></div><div style='height:10mm;background:linear-gradient(90deg,#1f4e79,#cfe0f1)'></div><div style='height:10mm;background:rgba(31,78,121,.3)'></div>",
    ),
    "image-object-fit-round": (
        "",
        "<img src='{photo}' style='width:30mm;height:20mm;object-fit:cover;border:1px solid #000'> <img src='{photo}' style='width:20mm;height:20mm;border-radius:50%'>",
    ),
    "position-absolute": (
        "",
        f"<div style='position:relative;height:30mm;{BOX}'><span style='position:absolute;right:2mm;bottom:2mm;{BOX}'>bottom right</span></div>",
    ),
    "position-fixed": (
        "",
        f"<div style='position:fixed;top:0;right:0;{BOX}'>fixed</div>body",
    ),
    "list-markers": (
        "li.c::marker{color:#b0413e}",
        "<ul><li class=c>disc, coloured marker</li><li>disc</li></ul><ol><li>decimal</li><li>decimal</li></ol><ul style=\"list-style-type:'+ '\"><li>string marker</li></ul>",
    ),
    "before-counter": (
        "ol.k{counter-reset:k;list-style:none;padding:0}ol.k li{counter-increment:k}ol.k li::before{content:counter(k) '. ';color:#b0413e;font-weight:700}",
        "<ol class=k><li>one</li><li>two</li><li>three</li></ol>",
    ),
    "overflow-wrap-anywhere": (
        "",
        "<div style='width:50mm;border:1px solid #000;overflow-wrap:anywhere'>See https://catalogue.internal.example.org/platform/services/overview/with/a/very/long/path for owners.</div>",
    ),
    "hyphens-auto": (
        "",
        "<div lang=en style='width:30mm;border:1px solid #000;hyphens:auto;text-align:justify'>Internationalization responsibilities characteristically overwhelm documentation.</div>",
    ),
    "text-transform-letter-spacing": (
        "",
        "<div style='text-transform:uppercase;letter-spacing:.2em'>Experience</div><div style='font-variant:small-caps'>Small Caps</div>",
    ),
    "box-shadow": (
        "",
        "<div style='width:40mm;height:12mm;background:#fff;box-shadow:2pt 2pt 4pt rgba(0,0,0,.5);border:1px solid #ccc'></div>",
    ),
    "transform": (
        "",
        f"<div style='width:30mm;margin:6mm;transform:rotate(-8deg);{BOX}'>rotated</div>",
    ),
    "opacity": ("", "<div style='background:#1f4e79;opacity:.4;height:10mm'></div>"),
    "aspect-ratio": ("", f"<div style='width:30mm;aspect-ratio:2 / 1;{BOX}'>2:1</div>"),
    "float": (
        "",
        f"<div style='float:left;width:20mm;height:14mm;margin-right:3pt;{BOX}'></div>"
        + "text that wraps around the float " * 6,
    ),
    "table": (
        "td{border:1px solid #1f4e79;padding:2pt}",
        "<table style='border-collapse:collapse;width:100%'><tr><td>a</td><td>b</td></tr><tr><td colspan=2>c</td></tr></table>",
    ),
    "text-overflow-ellipsis": (
        "",
        "<div style='width:40mm;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border:1px solid #000'>a line far too long for the box it sits in</div>",
    ),
    "inline-svg": (
        "",
        "<svg width='60' height='30' viewBox='0 0 60 30'><circle cx='15' cy='15' r='12' fill='#1f4e79'/><path d='M32 25 L45 5 L58 25 Z' fill='#b0413e'/></svg> svg icon",
    ),
    "text-align-justify": (
        "",
        "<div style='width:60mm;text-align:justify'>"
        + "Justified text spreads the words of each line evenly between the margins. "
        * 2
        + "</div>",
    ),
    "filter": (
        "",
        "<img src='{photo}' style='width:20mm;filter:grayscale(1) blur(1px)'>",
    ),
}


def probe(name: str, fonts: str, photo: str) -> str:
    css, body = PROBES[name]
    fonts = "data" if name == "font-face-data" else fonts
    page = (
        f"<!doctype html><html lang=en><head><meta charset=utf-8><style>{font_css_cache[fonts]}{PROBE_PAGE}{css}</style></head>"
        f"<body><div style='font-size:7pt;color:#888;margin-bottom:3pt'>{name}</div>{body.replace('{photo}', photo)}</body></html>"
    )
    return page if fonts == "data" else installed(page)


EDGE_OUTSIDE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<link rel=stylesheet href="http://192.0.2.10/sheet.css">
<style>@import url("http://192.0.2.10/import.css");
.bg{background:url("http://192.0.2.10/bg.png");height:10mm}
html{font-family:"Tpl Sans"}</style></head><body>
<p>Every address below is outside: none may be fetched.</p>
<img src="https://outside.example.org/pixel.png" alt="">
<div class=bg></div><object data="http://192.0.2.10/object"></object>
<iframe src="http://192.0.2.10/frame"></iframe></body></html>"""

font_css_cache: dict[str, str] = {}


def main(fixtures: Path, out: Path) -> None:
    target = out / "html"
    target.mkdir(parents=True, exist_ok=True)
    font_css_cache["data"] = font_css(subset_fonts())
    font_css_cache["installed"] = ""
    photo = placeholder_photo()
    profile = json.loads((fixtures / "profile.json").read_text())
    index = {"fixtures": [], "probes": list(PROBES), "edges": []}
    for path in sorted(fixtures.glob("*.json")):
        if path.name == "profile.json":
            continue
        doc = json.loads(path.read_text())
        for layout in ("one", "two"):
            name = f"{path.stem}.{layout}"
            index["fixtures"].append(name)
            for step in range(STEPS):
                (target / f"{name}.s{step}.html").write_text(
                    document(doc, profile, layout, step, "installed", photo)
                )
    for name in PROBES:
        (target / f"probe-{name}.html").write_text(probe(name, "installed", photo))
    typical = json.loads((fixtures / "cv-typical.json").read_text())
    page = document(typical, profile, "one", 0, "installed", photo)
    edges = {
        # the body asks for a family that is not installed: the renderer falls back to another
        # font, and only the fonts embedded in the PDF tell (compare.py fails it loudly)
        "edge-missing-font": page.replace(
            'html{font-family:"DejaVu Sans"', 'html{font-family:"Tpl Missing"'
        ),
        # the template's fonts as @font-face from data: (WeasyPrint needs a temporary file)
        "edge-data-font": document(typical, profile, "one", 0, "data", photo),
        "edge-outside": installed(EDGE_OUTSIDE),
    }
    for name, text in edges.items():
        (target / f"{name}.html").write_text(text)
        index["edges"].append(name)
    (target / "index.json").write_text(json.dumps(index, indent=1))
    print(
        f"{len(index['fixtures'])} fixture layouts x {STEPS} steps, {len(PROBES)} probes, {len(edges)} edges"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
