import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DocumentSheet } from "@/components/shared/documents/document-sheet";
import { LAYOUTS } from "@/components/shared/documents/document-sheet/layouts";
import { DocumentForm } from "@/design-system/fixtures/document-form";
import {
  SAMPLE_CV,
  SAMPLE_DAY,
  SAMPLE_LETTER,
  SAMPLE_PROFILE,
} from "@/design-system/fixtures/template-sample";
import type { Draft } from "@/features/documents/helpers/edit-content";
import { pageFacts } from "@/features/documents/helpers/page-facts";

const here = path.dirname(fileURLToPath(import.meta.url));
const TEMPLATES = path.resolve(here, "../../../../api/app/documents/templates");
const SLUGS = Object.keys(LAYOUTS).sort();
const KINDS = ["cv", "letter"] as const;
type Kind = (typeof KINDS)[number];

/** The classes each macro of _base prints, by default or given as its argument. */
const MACROS: Record<string, string[]> = {
  header: ["head", "photo", "headtext", "name", "headline", "contact", "word"],
  employer: ["entry", "entry-head", "entry-title", "org", "period", "place"],
  school: ["school", "entry-title", "org", "period"],
  skill_group: ["skill-group", "group-label", "items"],
  paragraphs: ["paragraph"],
  letter_date: ["date"],
  contact_column: ["block", "contact", "small"],
  contact_across: ["contact", "across"],
  contact_cells: ["contact", "span1", "span2", "span3"],
};
const DEFAULTS: Record<string, string> = {
  words: "word",
  contact_lines: "contact",
  portrait: "photo",
};

/** Every class a template's cv.html.j2 or letter.html.j2 prints, its macros' included. */
function printed(slug: string, kind: Kind): Set<string> {
  const text = readFileSync(
    path.join(TEMPLATES, slug, `${kind}.html.j2`),
    "utf-8",
  ).replace(/\{#[\s\S]*?#\}/g, "");
  const out = new Set<string>();
  const add = (words: string) => {
    for (const one of words.split(/\s+/)) if (one !== "") out.add(one);
  };
  for (const match of text.matchAll(/class="([^"]*)"/g))
    add(
      (match[1] ?? "")
        .replace(/\{%[\s\S]*?%\}/g, " ")
        .replace(/\{\{[\s\S]*?\}\}/g, " "),
    );
  for (const match of text.matchAll(/\b(?:base|layout)\.(\w+)\(([^)]*)\)/g)) {
    const [, macro = "", args = ""] = match;
    if (macro === "label") continue;
    const given = [...args.matchAll(/"([^"]*)"/g)].map((one) => one[1] ?? "");
    given.forEach(add);
    if (given.length === 0 && macro in DEFAULTS) add(DEFAULTS[macro] ?? "");
    (MACROS[macro] ?? []).forEach(add);
  }
  // A template without a place for the photo prints none, whatever the profile has.
  const manifest = JSON.parse(
    readFileSync(path.join(TEMPLATES, slug, "manifest.json"), "utf-8"),
  ) as { photo: string };
  if (manifest.photo === "none") out.delete("photo");
  return out;
}

const facts = (identity: Partial<typeof SAMPLE_PROFILE.identity> = {}) =>
  pageFacts(
    {
      ...SAMPLE_PROFILE,
      identity: { ...SAMPLE_PROFILE.identity, ...identity },
    },
    {},
  );

const LONG_LINKS = [
  { label: "a", url: "https://example.org/a-rather-long-path-for-the-page" },
  {
    label: "b",
    url: "https://example.org/an-even-longer-path-that-takes-the-whole-row-of-cells",
  },
];

/** The page read with the facts that show each conditional class of the templates. */
const VARIANTS = [
  { facts: facts(), photo: "data:image/png;base64,AAAA" },
  {
    facts: facts({ website: "https://example.org/alex-example-x" }),
    photo: null,
  },
  { facts: facts({ links: LONG_LINKS }), photo: null },
  {
    facts: facts({ full_name: "Alexandra Example Sample Demonstration" }),
    photo: null,
  },
  {
    facts: facts({
      full_name:
        "Alexandra Maria Example Sample Demonstration Placeholder Fictional",
    }),
    photo: null,
  },
  { facts: facts({ full_name: null }), photo: null },
];

const content = (kind: Kind): Draft =>
  kind === "cv" ? SAMPLE_CV : SAMPLE_LETTER;

function drawn(slug: string, kind: Kind): Set<string> {
  const out = new Set<string>();
  for (const variant of VARIANTS) {
    const { unmount } = render(
      <DocumentSheet
        kind={kind}
        language="en"
        template={slug}
        content={content(kind)}
        facts={variant.facts}
        photo={variant.photo}
        form={null}
        known
        date={SAMPLE_DAY}
      />,
    );
    const root = screen
      .getByTestId("document-sheet")
      .querySelector("[data-template-sheet]");
    for (const element of root?.querySelectorAll("[class]") ?? [])
      for (const one of element.classList) out.add(one);
    unmount();
  }
  return out;
}

describe("each template's page drawn here", () => {
  it.each(SLUGS.flatMap((slug) => KINDS.map((kind) => [slug, kind] as const)))(
    "%s, the %s: prints the classes its Jinja template prints, and no other",
    (slug, kind) => {
      const expected = printed(slug, kind);
      const got = drawn(slug, kind);
      expect([...expected].filter((one) => !got.has(one))).toEqual([]);
      expect([...got].filter((one) => !expected.has(one))).toEqual([]);
    },
  );

  it.each(SLUGS)(
    "%s: every written part of the CV is edited in place, Escape puts its text back",
    async (slug) => {
      const user = userEvent.setup();
      render(
        <DocumentForm initial={SAMPLE_CV}>
          {(form) => (
            <DocumentSheet
              kind="cv"
              language="en"
              template={slug}
              content={form.draft}
              facts={facts()}
              form={form}
              known
            />
          )}
        </DocumentForm>,
      );
      const sheet = screen.getByTestId("document-sheet");
      for (const part of [
        "headline",
        "summary",
        "highlights",
        "competencies",
        "experience",
        "skills",
      ])
        expect(
          within(sheet).getByRole("button", { name: `Edit the ${part}` }),
        ).toBeVisible();

      await user.click(
        within(sheet).getByRole("button", { name: "Edit the summary" }),
      );
      const summary = within(sheet).getByLabelText("Summary");
      expect(summary).toHaveFocus();
      await user.clear(summary);
      await user.type(summary, "Changed");
      await user.keyboard("{Escape}");
      expect(within(sheet).queryByLabelText("Summary")).toBeNull();
      expect(sheet).toHaveTextContent(String(SAMPLE_CV.summary));
      expect(
        within(sheet).getByRole("button", { name: "Edit the summary" }),
      ).toHaveFocus();

      await user.keyboard("{Enter}");
      await user.type(within(sheet).getByLabelText("Summary"), " Kept.");
      await user.click(within(sheet).getByRole("button", { name: "Done" }));
      expect(sheet).toHaveTextContent(`${String(SAMPLE_CV.summary)} Kept.`);
    },
  );

  it.each(SLUGS)(
    "%s: every written part of the letter is edited in place",
    async (slug) => {
      const user = userEvent.setup();
      render(
        <DocumentForm initial={SAMPLE_LETTER} kind="letter">
          {(form) => (
            <DocumentSheet
              kind="letter"
              language="en"
              template={slug}
              content={form.draft}
              facts={facts()}
              form={form}
              known
            />
          )}
        </DocumentForm>,
      );
      const sheet = screen.getByTestId("document-sheet");
      for (const part of [
        "headline",
        "recipient",
        "subject",
        "salutation",
        "paragraphs",
        "closing",
      ])
        expect(
          within(sheet).getByRole("button", { name: `Edit the ${part}` }),
        ).toBeVisible();
      await user.click(
        within(sheet).getByRole("button", { name: "Edit the paragraphs" }),
      );
      expect(within(sheet).getByLabelText("Paragraph 1")).toHaveFocus();
    },
  );

  it("leaves out of a page only read the parts with no text, as the PDF does", () => {
    for (const slug of SLUGS) {
      const { unmount } = render(
        <DocumentSheet
          kind="cv"
          language="en"
          template={slug}
          content={{
            ...SAMPLE_CV,
            summary: "",
            highlights: [],
            competencies: [],
            skills: [],
          }}
          facts={facts()}
          form={null}
          known
        />,
      );
      const sheet = screen.getByTestId("document-sheet");
      for (const heading of ["Profile", "Selected achievements", "Skills"])
        expect(
          within(sheet).queryByRole("heading", { name: heading }),
          `${slug}: ${heading}`,
        ).toBeNull();
      expect(sheet, slug).not.toHaveTextContent(/Add /);
      expect(within(sheet).queryAllByRole("button"), slug).toEqual([]);
      unmount();
    }
  });

  it("prints its headings and the date in French", () => {
    render(
      <DocumentSheet
        kind="letter"
        language="fr"
        template="classic"
        content={SAMPLE_LETTER}
        facts={facts()}
        form={null}
        known
        date={new Date(2026, 10, 1)}
      />,
    );
    const sheet = screen.getByTestId("document-sheet");
    expect(sheet).toHaveTextContent("1er novembre 2026");
    expect(sheet.textContent).toContain("Objet\u00a0:");
  });

  it("draws a template it does not know with Classic, and says so", () => {
    render(
      <DocumentSheet
        kind="cv"
        language="en"
        template="mine-3f2a"
        content={SAMPLE_CV}
        facts={facts()}
        form={null}
        known
      />,
    );
    expect(
      screen
        .getByTestId("document-sheet")
        .querySelector('[data-template-sheet="classic"]'),
    ).not.toBeNull();
    expect(
      screen.getByText(/This page is drawn with Classic/),
    ).toBeInTheDocument();
  });

  it("names the page's headings under the app's: no second level one heading", () => {
    render(
      <DocumentSheet
        kind="cv"
        language="en"
        template="sidebar"
        content={SAMPLE_CV}
        facts={facts()}
        form={null}
        known
      />,
    );
    expect(screen.queryAllByRole("heading", { level: 1 })).toEqual([]);
    expect(
      screen.getByRole("heading", { level: 3, name: "Experience" }),
    ).toBeInTheDocument();
  });
});
