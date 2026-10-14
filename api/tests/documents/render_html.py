"""The HTML of D1's content fixtures at a density step, for the renderer's tests and for
tests/documents_render_jetson.py. A stand in for the template system (task D4a), nothing more:
one column, the fixtures' invented profile ("Alex Example"), DejaVu (the family the renderer's
image installs), eight steps loosest first, every value escaped.

    build(name, step) -> str     name: a file of fixtures/content without .json
    FIXTURES                     the fixtures in the order of the build table
"""

import html
import json
from pathlib import Path

CONTENT = Path(__file__).parent / "fixtures" / "content"
FIXTURES = ("cv-short", "cv-typical", "cv-long", "cv-tightest", "letter-en", "letter-fr")
STEPS = tuple(range(8))
E = html.escape

LABELS = {
    "en": {
        "summary": "Profile",
        "highlights": "Highlights",
        "competencies": "Competencies",
        "experience": "Experience",
        "skills": "Skills",
        "education": "Education",
        "languages": "Languages",
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
        "present": "aujourd'hui",
        "to": "à",
        "date": "14 octobre 2026",
    },
}
MONTHS = {
    "en": "January February March April May June July August September October November December",
    "fr": "janvier février mars avril mai juin juillet août septembre octobre novembre décembre",
}

CSS = """
@page{size:A4;margin:%(m)dmm}
:root{--fs:%(fs).2fpt;--lh:%(lh).2f;--gap:%(gap).1fpt;--accent:#1f4e79;--muted:#5a6270}
html{font-family:"DejaVu Sans";font-size:var(--fs);line-height:var(--lh);color:#1d232b}
body{margin:0}
h1{font-family:"DejaVu Serif";font-weight:400;font-size:24pt;line-height:1.1;margin:0}
h2{font-size:var(--fs);text-transform:uppercase;letter-spacing:.08em;color:var(--accent);
   border-bottom:1px solid #c9d3df;margin:0 0 3pt;padding-bottom:1pt}
header{display:flex;justify-content:space-between;align-items:flex-end;
   border-bottom:2px solid var(--accent);padding-bottom:4pt}
section{margin-top:var(--gap)}
p{margin:0 0 4pt}
ul{margin:0;padding-left:1.2em}
li{overflow-wrap:anywhere}
.headline{color:var(--accent);font-weight:700;margin-top:2pt}
.contacts{text-align:right;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:3pt 4pt}
.chips span{border:1px solid var(--accent);border-radius:8pt;padding:0 5pt}
.job{margin-bottom:4pt}
.job-head{display:flex;justify-content:space-between;gap:8pt}
.muted{color:var(--muted)}
.skills{display:grid;grid-template-columns:auto 1fr;gap:2pt 8pt}
.cols2{columns:2;column-gap:14pt}
"""


def density(step: int) -> dict[str, float]:
    return {
        "fs": 10.5 - 0.25 * step,
        "lh": 1.42 - 0.03 * step,
        "gap": 11 - 1.2 * step,
        "m": 16 - step,
    }


def load(name: str) -> dict:
    return json.loads((CONTENT / f"{name}.json").read_text(encoding="utf-8"))


def period(start: str | None, end: str | None, lang: str) -> str:
    def one(value: str | None) -> str:
        if not value:
            return LABELS[lang]["present"]
        year, _, month = value.partition("-")
        return f"{MONTHS[lang].split()[int(month) - 1]} {year}" if month else year

    return f"{one(start)} {LABELS[lang]['to']} {one(end)}"


def cv_body(c: dict, profile: dict, lang: str) -> str:
    labels, fixed = LABELS[lang], profile["fixed"]
    employers = {e["key"]: e for e in fixed["employers"]}
    out = [f"<section><h2>{labels['summary']}</h2><p>{E(c['summary'])}</p></section>"]
    if c.get("highlights"):
        items = "".join(f"<li>{E(h)}</li>" for h in c["highlights"])
        out.append(f"<section><h2>{labels['highlights']}</h2><ul>{items}</ul></section>")
    if c.get("competencies"):
        chips = "".join(f"<span>{E(x)}</span>" for x in c["competencies"])
        out.append(
            f"<section><h2>{labels['competencies']}</h2><div class=chips>{chips}</div></section>"
        )
    jobs = []
    for entry in c["experience"]:
        e = employers[entry["employer_key"]]
        bullets = "".join(f"<li>{E(b)}</li>" for b in entry["bullets"])
        jobs.append(
            f"<div class=job><div class=job-head><span><b>{E(e['organisation'])}</b>, "
            f"{E(e['title'])}</span><span class=muted>"
            f"{E(period(e['start'], e['end'], lang))}</span></div><ul>{bullets}</ul></div>"
        )
    out.append(f"<section><h2>{labels['experience']}</h2>{''.join(jobs)}</section>")
    skills = "".join(
        f"<b>{E(s['label'])}</b><span>{E(', '.join(s['items']))}</span>" for s in c["skills"]
    )
    out.append(f"<section><h2>{labels['skills']}</h2><div class=skills>{skills}</div></section>")
    schools = "".join(
        f"<div><b>{E(s['degree'])}</b><br><span class=muted>{E(s['name'])}, "
        f"{E(s['start'])} {labels['to']} {E(s['end'])}</span></div>"
        for s in fixed["schools"]
    )
    langs = "".join(
        f"<div>{E(x['name'])} <span class=muted>({E(x['level'])})</span></div>"
        for x in fixed["languages"]
    )
    out.append(
        f"<div class=cols2><section><h2>{labels['education']}</h2>{schools}</section>"
        f"<section><h2>{labels['languages']}</h2>{langs}</section></div>"
    )
    return "".join(out)


def letter_body(c: dict, lang: str) -> str:
    paragraphs = "".join(f"<p style='text-align:justify'>{E(p)}</p>" for p in c["paragraphs"])
    return (
        f"<p class=muted style='margin-top:var(--gap)'>{E(c['recipient'])}<br>"
        f"{LABELS[lang]['date']}</p><p><b>{E(c['subject'])}</b></p><p>{E(c['salutation'])}</p>"
        f"{paragraphs}<p>{E(c['closing'])}</p>"
    )


def build(name: str, step: int, profile: dict | None = None) -> str:
    doc = load(name)
    profile = profile or load("profile")
    c, lang, ident = doc["content"], doc["language"], profile["identity"]
    contacts = [ident["email"], ident["phone"], ident["location"], *ident["links"]]
    body = cv_body(c, profile, lang) if doc["kind"] == "cv" else letter_body(c, lang)
    return (
        f"<!doctype html><html lang={lang}><head><meta charset=utf-8>"
        f"<style>{CSS % density(step)}</style></head><body><header><div>"
        f"<h1>{E(ident['name'])}</h1><div class=headline>{E(c['headline'])}</div></div>"
        f"<div class=contacts>{'<br>'.join(E(x) for x in contacts)}</div></header>"
        f"{body}</body></html>"
    )
