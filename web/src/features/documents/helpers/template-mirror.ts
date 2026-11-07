/**
 * Generated from api/app/documents/templates (each manifest.json and style.css, the icons of
 * _icons) by test/template-mirror.test.ts, never by hand: after a change there, run (in web/)
 * pnpm exec vitest run src/features/documents/test/template-mirror.test.ts --update=all
 */
export interface MirrorStep {
  body_size: number;
  line_height: number;
  section_gap: number;
  item_gap: number;
  margin: number;
}

export interface MirroredTemplate {
  /** Font families of api/app/documents/fonts, the first one the main family. */
  fonts: readonly string[];
  accent: string;
  photo: "required" | "optional" | "none";
  /** The template's own section labels per language, over the shared ones of labels.json. */
  labels: {
    readonly en?: Readonly<Record<string, string>>;
    readonly fr?: Readonly<Record<string, string>>;
  };
  /** The density steps, loosest first: a version's `fit_step` is an index. */
  steps: { cv: readonly MirrorStep[]; letter: readonly MirrorStep[] };
  css: string;
}

export const TEMPLATE_MIRROR: Readonly<Record<string, MirroredTemplate>> = {
  band: {
    fonts: ["inter-tight"],
    accent: "#262525",
    photo: "optional",
    labels: {
      en: {
        summary: "About",
        competencies: "Skills",
        experience: "Professional Experience",
        education: "Education",
        highlights: "Key Achievements",
      },
      fr: {
        summary: "À propos",
        competencies: "Compétences",
        experience: "Expériences Professionnelles",
        education: "Formation",
        highlights: "Réalisations Clés",
      },
    },
    steps: {
      cv: [
        {
          body_size: 10,
          line_height: 1.333,
          section_gap: 19,
          item_gap: 12.4,
          margin: 15.7,
        },
        {
          body_size: 9.71,
          line_height: 1.311,
          section_gap: 16.9,
          item_gap: 11.1,
          margin: 14.9,
        },
        {
          body_size: 9.43,
          line_height: 1.289,
          section_gap: 14.7,
          item_gap: 9.7,
          margin: 14.1,
        },
        {
          body_size: 9.14,
          line_height: 1.267,
          section_gap: 12.6,
          item_gap: 8.4,
          margin: 13.3,
        },
        {
          body_size: 8.86,
          line_height: 1.246,
          section_gap: 10.4,
          item_gap: 7,
          margin: 12.4,
        },
        {
          body_size: 8.57,
          line_height: 1.224,
          section_gap: 8.3,
          item_gap: 5.7,
          margin: 11.6,
        },
        {
          body_size: 8.29,
          line_height: 1.202,
          section_gap: 6.1,
          item_gap: 4.3,
          margin: 10.8,
        },
        {
          body_size: 8,
          line_height: 1.18,
          section_gap: 4,
          item_gap: 3,
          margin: 10,
        },
      ],
      letter: [
        {
          body_size: 10.5,
          line_height: 1.45,
          section_gap: 14,
          item_gap: 9,
          margin: 15.7,
        },
        {
          body_size: 10.2,
          line_height: 1.416,
          section_gap: 12.6,
          item_gap: 8,
          margin: 15,
        },
        {
          body_size: 9.9,
          line_height: 1.382,
          section_gap: 11.2,
          item_gap: 7,
          margin: 14.2,
        },
        {
          body_size: 9.6,
          line_height: 1.348,
          section_gap: 9.8,
          item_gap: 6,
          margin: 13.5,
        },
        {
          body_size: 9.3,
          line_height: 1.314,
          section_gap: 8.4,
          item_gap: 5,
          margin: 12.7,
        },
        {
          body_size: 9,
          line_height: 1.28,
          section_gap: 7,
          item_gap: 4,
          margin: 12,
        },
      ],
    },
    css: '@page { size: A4; background: #f8f8f8; }\n* { box-sizing: border-box; }\nhtml {\nfont-family: "Inter Tight", sans-serif;\nfont-size: var(--body-size);\nline-height: var(--line-height);\ncolor: #262525;\n}\nbody { margin: 0; }\np, ul, h1, h2, h3 { margin: 0; padding: 0; font-weight: inherit; font-size: inherit; }\na { color: inherit; text-decoration: none; }\nli, p, h3 { overflow-wrap: anywhere; }\n.band {\ndisplay: flex;\nflex-wrap: wrap;\nalign-items: flex-start;\ngap: 12pt;\nmargin: 10.5pt 2pt 0 0;\npadding: 1.43em 13pt 1.36em 14.9pt;\nbackground: #262525;\ncolor: #f8f8f8;\nborder-radius: 8pt;\nmargin-bottom: 0.3pt;\n}\n.photo {\nwidth: 40pt;\nheight: 40pt;\nobject-fit: cover;\nborder-radius: 50%;\nflex: 0 0 auto;\n}\n.who { flex: 1 1 0; min-width: min-content; }\n.name { font-size: 1.8em; font-weight: 700; line-height: 1.1; }\n.headline { font-size: 1.3em; font-weight: 700; line-height: 1.15; }\n.contact {\nlist-style: none;\ndisplay: flex;\nflex-direction: column;\nalign-items: flex-end;\ntext-align: right;\nline-height: 1.35;\nmargin-top: 1.5pt;\nflex: 0 0 auto;\nmax-width: 70%;\n}\n.contact .phone { order: 1; }\n.contact .email { order: 2; }\n.contact .place { order: 3; }\n.contact .link { order: 4; }\n.row, .letter-body { margin: 0 15pt 0 14.9pt; }\n.row {\ndisplay: flex;\nmargin-top: 2.4pt;\npadding-top: var(--section-gap);\n}\n.row.group { margin-top: 0; padding-top: var(--item-gap); }\n.summary { line-height: 1.35; }\n.label {\nflex: 0 0 92.7pt;\npadding-right: 8pt;\nfont-weight: 700;\n}\n.body { flex: 1 1 auto; min-width: 0; }\n.dots { list-style: none; font-size: 0.9em; }\n.dots li { position: relative; padding-left: 15.3pt; }\n.dots li::before {\ncontent: "";\nposition: absolute;\nleft: 3.8pt;\ntop: 0.5em;\nwidth: 3pt;\nheight: 3pt;\nborder-radius: 50%;\nbackground: #262525;\n}\n.rule {\nmargin: var(--section-gap) 15pt 0 107.6pt;\nborder-top: 1.5pt solid #262525;\n}\n.rule + .row { margin-top: 4.1pt; padding-top: var(--item-gap); }\n.entry + .entry { margin-top: var(--item-gap); }\n.entry .dots { padding-bottom: 4.2pt; }\n.entry:last-child .dots { padding-bottom: 2.5pt; }\n.title { font-weight: 700; }\n.meta { font-size: 0.8em; font-style: italic; margin-top: 3pt; line-height: 1.34; }\n.meta + .meta { margin-top: 0; }\n.entry .dots { margin-top: 3.8pt; }\n.items { font-size: 0.9em; }\n.letter-body { padding: 28pt 0 0 92.7pt; }\n.letter-body p { margin-bottom: var(--item-gap); }\n.date { text-align: right; }\n.recipient { margin-top: var(--section-gap); }\n.subject { margin: var(--section-gap) 0; }\n.subject strong { font-weight: 700; }\n.paragraph { white-space: pre-line; }\n.closing { margin-top: var(--section-gap); }\n.signature { font-weight: 700; margin-top: var(--section-gap); }\n.word { display: inline-block; max-width: 100%; overflow-wrap: anywhere; }\n.name { white-space: nowrap; }\n.name.long { white-space: normal; }\n.name .word { white-space: nowrap; }\n.name.longword { font-size: 1.2em; }\n.name.longword .word { white-space: normal; }',
  },
  ledger: {
    fonts: ["gantari"],
    accent: "#000000",
    photo: "optional",
    labels: {
      en: {
        summary: "About me",
        highlights: "Recognition",
        competencies: "Core Skills",
        experience: "Experience",
        skills: "Skills",
        education: "Education",
        languages: "Languages",
      },
      fr: {
        summary: "À propos",
        highlights: "Distinctions",
        competencies: "Compétences clés",
        experience: "Expérience",
        skills: "Compétences",
        education: "Formation",
        languages: "Langues",
      },
    },
    steps: {
      cv: [
        {
          body_size: 10,
          line_height: 1.3,
          section_gap: 17.7,
          item_gap: 11.8,
          margin: 11.8,
        },
        {
          body_size: 9.8,
          line_height: 1.286,
          section_gap: 16.2,
          item_gap: 10.8,
          margin: 11.4,
        },
        {
          body_size: 9.6,
          line_height: 1.271,
          section_gap: 14.6,
          item_gap: 9.9,
          margin: 11,
        },
        {
          body_size: 9.4,
          line_height: 1.257,
          section_gap: 13.1,
          item_gap: 8.9,
          margin: 10.6,
        },
        {
          body_size: 9.2,
          line_height: 1.243,
          section_gap: 11.6,
          item_gap: 7.9,
          margin: 10.2,
        },
        {
          body_size: 9,
          line_height: 1.229,
          section_gap: 10.1,
          item_gap: 6.9,
          margin: 9.8,
        },
        {
          body_size: 8.8,
          line_height: 1.214,
          section_gap: 8.5,
          item_gap: 6,
          margin: 9.4,
        },
        {
          body_size: 8.6,
          line_height: 1.2,
          section_gap: 7,
          item_gap: 5,
          margin: 9,
        },
      ],
      letter: [
        {
          body_size: 10.5,
          line_height: 1.45,
          section_gap: 14,
          item_gap: 9,
          margin: 11.8,
        },
        {
          body_size: 10.2,
          line_height: 1.416,
          section_gap: 12.6,
          item_gap: 8,
          margin: 11.3,
        },
        {
          body_size: 9.9,
          line_height: 1.382,
          section_gap: 11.2,
          item_gap: 7,
          margin: 10.7,
        },
        {
          body_size: 9.6,
          line_height: 1.348,
          section_gap: 9.8,
          item_gap: 6,
          margin: 10.1,
        },
        {
          body_size: 9.3,
          line_height: 1.314,
          section_gap: 8.4,
          item_gap: 5,
          margin: 9.6,
        },
        {
          body_size: 9,
          line_height: 1.28,
          section_gap: 7,
          item_gap: 4,
          margin: 9,
        },
      ],
    },
    css: '@page { size: A4; background: #fdfbf7; }\n* { box-sizing: border-box; }\nhtml {\nfont-family: "Gantari", sans-serif;\nfont-size: var(--body-size);\nline-height: var(--line-height);\ncolor: #000000;\n}\nbody { margin: 0; }\np, ul, h1, h2, h3 { margin: 0; padding: 0; font-weight: inherit; font-size: inherit; }\na { color: inherit; text-decoration: none; }\nli, p, h3 { overflow-wrap: anywhere; }\n.heavy { border-top: 2.2pt solid #000000; }\n.head { display: flex; flex-wrap: wrap; align-items: flex-start; padding: 16.0pt 0 14.3pt; }\n.photo {\nflex: 0 0 auto;\nwidth: 46pt;\nheight: 46pt;\nobject-fit: cover;\nborder-radius: 50%;\nmargin-right: 12pt;\n}\n.who { flex: 1 1 0; min-width: min-content; padding-right: 12pt; }\n.name {\nfont-size: 1.7em;\nfont-weight: 700;\nline-height: 1.15;\nletter-spacing: 0.04em;\ntext-transform: uppercase;\n}\n.name.longword { font-size: 1.2em; }\n.headline { margin-top: 0.8pt; font-weight: 700; letter-spacing: 0.01em; line-height: 1.3; }\n.contact {\nflex: 0 0 auto;\nmin-width: 211.2pt;\nmax-width: 100%;\ndisplay: grid;\ngrid-template-columns: minmax(105.6pt, max-content) minmax(105.6pt, max-content);\ngrid-auto-flow: column;\ngrid-template-rows: auto auto auto;\nlist-style: none;\nfont-size: 0.8em;\nline-height: 1.34;\nmargin-top: 1pt;\n}\n.contact li { padding-right: 4pt; }\n.contact .phone { order: 1; }\n.contact .email { order: 2; }\n.contact .place { order: 3; }\n.contact .link { order: 4; }\n.row {\ndisplay: flex;\npadding: var(--section-gap) 0 13pt;\nborder-top: 0.8pt solid #a6a6a6;\n}\n.heavy + .row { border-top: 0; padding-bottom: 16pt; }\n.label {\nflex: 0 0 105.7pt;\npadding-right: 8pt;\nfont-weight: 700;\nletter-spacing: 0.04em;\ntext-transform: uppercase;\n}\n.body { flex: 1 1 auto; min-width: 0; }\n.summary { font-size: 1.1em; line-height: 1.364; }\n.pair { display: flex; margin-top: 9.2pt; }\n.half { flex: 0 0 212.9pt; padding-right: 8pt; }\n.half + .half { flex: 1 1 auto; padding-right: 0; }\n.title { font-weight: 700; line-height: 1.27; }\n.lines { list-style: none; font-size: 0.8em; line-height: 1.34; margin-top: 4.6pt; }\n.lines.two { column-count: 2; column-gap: 0; }\n.lines.two li { break-inside: avoid; padding-right: 4pt; }\n.role + .role { margin-top: var(--item-gap); }\n.meta { font-size: 0.8em; line-height: 1.34; margin-top: 3.8pt; }\n.text { font-size: 0.8em; line-height: 1.34; }\n.meta + .text { margin-top: 4.9pt; }\n.cells { display: flex; flex-wrap: wrap; }\n.cells .lines { margin-top: 3.8pt; }\n.cell { flex: 0 0 25%; padding-right: 10pt; }\n.cells .cell:nth-child(n+5) { margin-top: var(--item-gap); }\n.cell.wide { flex-basis: 100%; }\n.cells .cell + .cell.wide, .cells .cell.wide + .cell { margin-top: var(--item-gap); }\n.lang { font-size: 0.8em; line-height: 1.34; padding-top: 1.9pt; }\n.letter-body { padding: 24pt 0 24pt 105.7pt; }\n.letter-body p { margin-bottom: var(--item-gap); }\n.date { text-align: right; }\n.recipient { margin-top: var(--section-gap); }\n.subject { margin: var(--section-gap) 0; }\n.subject strong { font-weight: 700; }\n.paragraph { white-space: pre-line; }\n.closing { margin-top: var(--section-gap); }\n.signature { font-weight: 700; margin-top: var(--section-gap); letter-spacing: 0.04em; text-transform: uppercase; }\n.word { display: inline-block; max-width: 100%; overflow-wrap: anywhere; }\n.name { white-space: nowrap; }\n.name.long { white-space: normal; }\n.name .word { white-space: nowrap; }\n.name.longword .word { white-space: normal; }',
  },
  "photo-columns": {
    fonts: ["raleway"],
    accent: "#07113c",
    photo: "optional",
    labels: {
      en: {
        summary: "Profile",
        education: "Education",
        skills: "Skills",
        languages: "Spoken languages",
        experience: "Experience",
        competencies: "Expertise",
        highlights: "Achievements",
      },
      fr: {
        summary: "Profil",
        education: "Formation",
        skills: "Compétences",
        languages: "Langues parlées",
        experience: "Expérience",
        competencies: "Expertise",
        highlights: "Réalisations",
      },
    },
    steps: {
      cv: [
        {
          body_size: 10,
          line_height: 1.5,
          section_gap: 15.3,
          item_gap: 8.2,
          margin: 8,
        },
        {
          body_size: 9.69,
          line_height: 1.454,
          section_gap: 13.6,
          item_gap: 7.3,
          margin: 7.7,
        },
        {
          body_size: 9.37,
          line_height: 1.409,
          section_gap: 11.9,
          item_gap: 6.4,
          margin: 7.4,
        },
        {
          body_size: 9.06,
          line_height: 1.363,
          section_gap: 10.2,
          item_gap: 5.5,
          margin: 7.1,
        },
        {
          body_size: 8.74,
          line_height: 1.317,
          section_gap: 8.6,
          item_gap: 4.7,
          margin: 6.9,
        },
        {
          body_size: 8.43,
          line_height: 1.271,
          section_gap: 6.9,
          item_gap: 3.8,
          margin: 6.6,
        },
        {
          body_size: 8.11,
          line_height: 1.226,
          section_gap: 5.2,
          item_gap: 2.9,
          margin: 6.3,
        },
        {
          body_size: 7.8,
          line_height: 1.18,
          section_gap: 3.5,
          item_gap: 2,
          margin: 6,
        },
      ],
      letter: [
        {
          body_size: 10.5,
          line_height: 1.55,
          section_gap: 14,
          item_gap: 9,
          margin: 8,
        },
        {
          body_size: 10.2,
          line_height: 1.504,
          section_gap: 12.6,
          item_gap: 8,
          margin: 7.6,
        },
        {
          body_size: 9.9,
          line_height: 1.458,
          section_gap: 11.2,
          item_gap: 7,
          margin: 7.2,
        },
        {
          body_size: 9.6,
          line_height: 1.412,
          section_gap: 9.8,
          item_gap: 6,
          margin: 6.8,
        },
        {
          body_size: 9.3,
          line_height: 1.366,
          section_gap: 8.4,
          item_gap: 5,
          margin: 6.4,
        },
        {
          body_size: 9,
          line_height: 1.32,
          section_gap: 7,
          item_gap: 4,
          margin: 6,
        },
      ],
    },
    css: '@page { size: A4; background: #ffffff; }\n* { box-sizing: border-box; }\nhtml {\nfont-family: "Raleway", sans-serif;\nfont-weight: 500;\nfont-size: var(--body-size);\nline-height: var(--line-height);\ncolor: #1a1917;\n}\nbody { margin: 0; }\np, ul, h1, h2, h3 { margin: 0; padding: 0; font-weight: inherit; font-size: inherit; }\na { color: inherit; text-decoration: none; }\nli, p, h3 { overflow-wrap: anywhere; }\n.head, .rule, .profile, .cols, .letter-body { margin-left: 9.4pt; margin-right: 9.4pt; }\n.head { display: flex; flex-wrap: wrap; align-items: flex-start; margin-top: 2.9pt; }\n.photo {\nflex: 0 0 auto;\nwidth: 101.7pt;\nheight: 118.8pt;\nobject-fit: cover;\nmargin-right: 21.8pt;\n}\n.who { flex: 1 1 0; padding-top: 16.3pt; min-width: min-content; }\n.name {\nfont-size: 2.6em;\nfont-weight: 700;\nline-height: 1.127;\nletter-spacing: 0.05em;\ntext-transform: uppercase;\n}\n.name.long { font-size: 2.0em; }\n.name.longword { font-size: 1.2em; }\n.headline {\nmargin-top: 11.3pt;\nfont-size: 1.205em;\nletter-spacing: 0.1em;\ntext-transform: uppercase;\n}\n.contact {\nflex: 0 0 auto;\nmin-width: 170pt;\nmax-width: 100%;\ndisplay: flex;\nflex-direction: column;\nlist-style: none;\nmargin: 22.7pt 0 0 0;\npadding-left: 12pt;\nfont-size: 0.9em;\nline-height: 1.25;\nletter-spacing: 0.03em;\n}\n.contact li { position: relative; padding-left: 23.2pt; margin-bottom: 9.75pt; }\n.contact li::before {\ncontent: "";\nposition: absolute;\nleft: 0;\ntop: 0;\nwidth: 13.1pt;\nheight: 11.2pt;\nbackground-position: center;\nbackground-repeat: no-repeat;\nbackground-size: contain;\n}\n.contact .email::before { background-image: var(--icon-email); height: 7.9pt; top: 1.6pt; }\n.contact .phone::before { background-image: var(--icon-phone); }\n.contact .link::before { background-image: var(--icon-link); }\n.contact .place::before { background-image: var(--icon-place); height: 12.8pt; top: -0.6pt; }\n.contact .email { order: 1; }\n.contact .phone { order: 2; }\n.contact .link { order: 3; }\n.contact .place { order: 4; }\n.rule { border-top: 0.75pt solid #1a1917; margin-top: var(--section-gap); }\n.heading {\nfont-size: 1.3em;\nfont-weight: 700;\nletter-spacing: 0.1em;\ntext-transform: uppercase;\nline-height: 1.2;\n}\n.profile { padding-top: 19.5pt; }\n.head { padding-bottom: 8.5pt; }\n.profile .heading { margin-bottom: 9.4pt; }\n.summary { line-height: 1.575; }\n.cols { display: flex; margin-top: 3.5pt; padding-top: var(--section-gap); }\n.left { flex: 0 0 199.9pt; margin-right: 32.2pt; }\n.cols.long .left { flex-basis: 48%; }\n.right { flex: 1 1 auto; min-width: 0; }\n.block { border-top: 0.75pt solid #1a1917; padding: 4.2pt 0 1.6pt; margin-bottom: var(--section-gap); }\n.block > .heading { margin-top: var(--section-gap); }\n.block:last-child { margin-bottom: 0; padding-bottom: 0; }\n.block .heading { margin-bottom: 10.2pt; }\n.title {\nfont-weight: 700;\nletter-spacing: 0.02em;\ntext-transform: uppercase;\n}\n.org { font-weight: 400; letter-spacing: 0.05em; }\n.period, .lines { letter-spacing: 0.05em; }\n.entry .title, .entry .org, .entry .period { padding-bottom: 0.03em; }\n.entry + .entry { margin-top: var(--item-gap); }\n.role + .role { margin-top: var(--section-gap); }\n.role .text + .text { margin-top: 1.5pt; }\n.role .org + .text { margin-top: 3.3pt; }\n.group + .group { margin-top: 8.3pt; }\n.lines { list-style: none; }\n.dots { list-style: none; color: #07113c; }\n.dots li { padding-bottom: 0.15em; }\n.dots li { position: relative; padding-left: 6.5pt; }\n.dots li::before {\ncontent: "";\nposition: absolute;\nleft: -4.5pt;\ntop: 0.6em;\nwidth: 3pt;\nheight: 3pt;\nborder-radius: 50%;\nbackground: #07113c;\n}\n.letter-body { padding-top: 26pt; }\n.letter-body p { margin-bottom: var(--item-gap); }\n.date { text-align: right; }\n.recipient { margin-top: var(--section-gap); }\n.subject { margin: var(--section-gap) 0; }\n.subject strong { font-weight: 700; }\n.paragraph { white-space: pre-line; }\n.closing { margin-top: var(--section-gap); }\n.signature { font-weight: 700; margin-top: var(--section-gap); letter-spacing: 0.05em; text-transform: uppercase; }\n.word { display: inline-block; max-width: 100%; overflow-wrap: anywhere; }\n.name { white-space: nowrap; }\n.name.long { white-space: normal; }\n.name .word { white-space: nowrap; }\n.name.longword .word { white-space: normal; }',
  },
};

/** The contact line icons, `--icon-<kind>` of every page (templates.icon_css). */
export const TEMPLATE_ICONS: Readonly<Record<string, string>> = {
  email:
    "url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNiAxNiI+PHBhdGggZmlsbD0iIzFhMTkxNyIgZD0iTTAgMGgyNnYxNkgweiIvPjxwYXRoIGQ9Ik0xIDEuMmwxMiA4LjMgMTItOC4zIiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iMS43Ii8+PC9zdmc+Cg==)",
  phone:
    "url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAyMiI+PHBhdGggZmlsbD0iIzFhMTkxNyIgZD0iTTMuMiAxLjFjMS0uOSAyLjQtLjcgMy4xLjRsMS43IDNjLjUgMSAuMyAyLjEtLjYgMi43TDYgOC4xYy43IDIuNCAxLjkgNC42IDMuNiA2LjNsMS40LS44YzEtLjUgMi4xLS4zIDIuNy42bDEuOSAyLjhjLjcgMSAuNSAyLjMtLjUgMy4xbC0xLjQgMUM4LjggMjIuNC45IDEyLjkuOSA0LjUuOSAzLjEgMS45IDIgMy4yIDEuMXoiLz48L3N2Zz4K)",
  place:
    "url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxOCAyNiI+PHBhdGggZmlsbD0iIzFhMTkxNyIgZmlsbC1ydWxlPSJldmVub2RkIiBkPSJNOSAwQzQgMCAwIDQgMCA5YzAgNi41IDkgMTcgOSAxN3M5LTEwLjUgOS0xN2MwLTUtNC05LTktOXptMCAxMi42YTMuNiAzLjYgMCAxIDEgMC03LjIgMy42IDMuNiAwIDAgMSAwIDcuMnoiLz48L3N2Zz4K)",
  link: "url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyMiAyMiI+PGcgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjMWExOTE3IiBzdHJva2Utd2lkdGg9IjEuNyI+PGNpcmNsZSBjeD0iMTEiIGN5PSIxMSIgcj0iOS42Ii8+PGVsbGlwc2UgY3g9IjExIiBjeT0iMTEiIHJ4PSI0LjMiIHJ5PSI5LjYiLz48cGF0aCBkPSJNMS40IDExaDE5LjJNMi44IDYuMmgxNi40TTIuOCAxNS44aDE2LjQiLz48L2c+PC9zdmc+Cg==)",
};
