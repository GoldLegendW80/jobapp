import type { ReactNode } from "react";

import type { Kind, Language } from "@/features/documents/api/documents-api";
import type { Draft } from "@/features/documents/helpers/edit-content";
import type { Form } from "@/features/documents/helpers/edit-form";
import type { PageFacts } from "@/features/documents/helpers/page-facts";
import {
  partName,
  partProblems,
  sheetPage,
  type Part,
} from "@/features/documents/helpers/sheet-page";
import {
  pageProperties,
  templateStyle,
} from "@/features/documents/helpers/template-css";
import { TEMPLATE_MIRROR } from "@/features/documents/helpers/template-mirror";
import { useTemplateFonts } from "@/features/documents/hooks/use-template-fonts";
import { DEFAULT_TEMPLATE, LAYOUTS } from "./document-sheet/layouts";
import { PageForm } from "./page-form";

const PARTS: Record<Kind, Part[]> = {
  cv: [
    "headline",
    "summary",
    "highlights",
    "competencies",
    "experience",
    "skills",
  ],
  letter: [
    "headline",
    "recipient",
    "subject",
    "salutation",
    "paragraphs",
    "closing",
  ],
};

const capitalized = (text: string) =>
  text.charAt(0).toUpperCase() + text.slice(1);

/** An A4 page's height in CSS pixels: the paper is at least one page long. */
const PAGE_HEIGHT = "calc(1122.5 * var(--tpl-px))";

/** The template drawn: the document's, else Classic, as the renderer prints an unknown one. */
function resolve(template: string, kind: Kind, step: number | null) {
  const drawn = template in LAYOUTS && template in TEMPLATE_MIRROR;
  const slug = drawn ? template : DEFAULT_TEMPLATE;
  const mirror = TEMPLATE_MIRROR[slug];
  const layout = LAYOUTS[slug];
  const steps = mirror?.steps[kind] ?? [];
  const density = steps[Math.min(Math.max(step ?? 0, 0), steps.length - 1)];
  if (mirror === undefined || layout === undefined || density === undefined)
    throw new Error(`The template ${slug} has no page here.`);
  return { drawn, slug, mirror, layout, density };
}

/**
 * The CV or the letter as its template prints it (document-sheet/templates/, one per template of
 * api/app/documents/templates), drawn here from its text and the profile: the same page is read
 * and, with a `form`, edited where it is read (in-place.tsx). The PDF stays the page that is sent.
 */
export function DocumentSheet({
  kind,
  language,
  template,
  step = null,
  photo = null,
  content,
  facts,
  form,
  known,
  date,
  children,
}: {
  kind: Kind;
  language: Language;
  /** The document's template; one not drawn here (a person's own) is drawn as Classic. */
  template: string;
  /** The density step the PDF was printed at (a version's `fit_step`); the loosest by default. */
  step?: number | null;
  /** The profile's photo, printed where the template has a place for it. */
  photo?: string | null;
  content: Draft;
  facts: PageFacts;
  /** The draft's form, to edit the page where it is read; null for a page only read. */
  form: Form | null;
  /** The profile is read: an employer missing from it can be said. */
  known: boolean;
  /** The day a letter is dated: today unless a story or a test fixes it. */
  date?: Date;
  /** Under the page's text, on the paper: nothing by default. */
  children?: ReactNode;
}) {
  const { drawn, slug, mirror, layout, density } = resolve(
    template,
    kind,
    step,
  );
  useTemplateFonts(mirror.fonts);
  const { css, margins } = templateStyle(slug, mirror.css);
  const page = sheetPage({
    kind,
    language,
    content,
    facts,
    photo: mirror.photo === "none" ? null : photo,
    form,
    known,
    now: date,
  });
  const notes =
    form === null
      ? []
      : PARTS[kind]
          .map((part) => ({ part, lines: partProblems(page, part) }))
          .filter((one) => one.lines.length > 0);
  const Layout = layout[kind];

  return (
    <PageForm.Provider value={form}>
      <div className="flex flex-col gap-3">
        {drawn ? null : (
          <p className="text-sm text-muted-foreground">
            This page is drawn with Classic: the printed page shows your own
            template.
          </p>
        )}
        {notes.length === 0 ? null : (
          <ul
            aria-label="Notes on the page"
            className="flex flex-col gap-1 text-sm text-warning"
          >
            {notes.flatMap((one) =>
              one.lines.map((line, index) => (
                <li key={`${one.part}-${String(index)}`}>
                  <span className="font-medium">{`${capitalized(partName(one.part))}:`}</span>{" "}
                  <span>{line}</span>
                </li>
              )),
            )}
          </ul>
        )}
        <div className="@container w-full">
          <article
            aria-label={kind === "cv" ? "The CV" : "The letter"}
            data-testid="document-sheet"
            className="relative w-full overflow-hidden rounded-md border bg-white shadow-sm"
            style={{
              ...pageProperties(density, mirror.accent),
              minHeight: PAGE_HEIGHT,
              paddingTop: margins.top,
              paddingRight: margins.right,
              paddingBottom: margins.bottom,
              paddingLeft: margins.left,
            }}
          >
            <style>{css}</style>
            <div data-template-sheet={slug} className={kind} lang={language}>
              <Layout page={page} />
            </div>
            {children}
          </article>
        </div>
      </div>
    </PageForm.Provider>
  );
}
