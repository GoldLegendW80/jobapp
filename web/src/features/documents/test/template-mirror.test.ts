import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as estree from "prettier/plugins/estree";
import * as typescript from "prettier/plugins/typescript";
import { format } from "prettier/standalone";
import { describe, expect, it } from "vitest";

import { LAYOUTS } from "@/components/shared/documents/document-sheet/layouts";
import { TEMPLATE_SAMPLE } from "@/design-system/fixtures/template-sample";

import { PAGE_LABELS } from "../helpers/page-labels";
import { TEMPLATE_MIRROR } from "../helpers/template-mirror";

const here = path.dirname(fileURLToPath(import.meta.url));
const DOCUMENTS = path.resolve(here, "../../../../../api/app/documents");
const TEMPLATES = path.join(DOCUMENTS, "templates");
const MIRROR_FILE = path.resolve(here, "../helpers/template-mirror.ts");
const FONTS_FILE = path.resolve(here, "../helpers/template-fonts.ts");
const PACKAGE = JSON.parse(
  readFileSync(path.resolve(here, "../../../../package.json"), "utf-8"),
) as { dependencies: Record<string, string> };

interface Manifest {
  slug: string;
  photo: string;
  fonts: string[];
  accent: string;
  labels?: Record<string, Record<string, string>>;
  cv: { steps: Record<string, number>[] };
  letter: { steps: Record<string, number>[] };
}

const read = (...parts: string[]) => readFileSync(path.join(...parts), "utf-8");

/** The template folders the api's registry serves (templates.discover). */
const slugs = readdirSync(TEMPLATES, { withFileTypes: true })
  .filter((one) => one.isDirectory() && !/^[_.]/.test(one.name))
  .map((one) => one.name)
  .sort();

const manifest = (slug: string) =>
  JSON.parse(read(TEMPLATES, slug, "manifest.json")) as Manifest;

/** style.css without its comments and blank lines: what the page here applies. */
const style = (slug: string) =>
  read(TEMPLATES, slug, "style.css")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "")
    .join("\n");

/** The contact line icons every template reads as `--icon-<kind>` (templates.icon_css). */
const ICON_KINDS = ["email", "phone", "place", "link"] as const;

const STEP = [
  "body_size",
  "line_height",
  "section_gap",
  "item_gap",
  "margin",
] as const;

async function mirrorSource(): Promise<string> {
  const mirror = Object.fromEntries(
    slugs.map((slug) => {
      const one = manifest(slug);
      const steps = (kind: "cv" | "letter") =>
        one[kind].steps.map((step) =>
          Object.fromEntries(STEP.map((key) => [key, step[key]])),
        );
      return [
        slug,
        {
          fonts: one.fonts,
          accent: one.accent,
          photo: one.photo,
          labels: one.labels ?? {},
          steps: { cv: steps("cv"), letter: steps("letter") },
          css: style(slug),
        },
      ];
    }),
  );
  const icons = Object.fromEntries(
    ICON_KINDS.map((kind) => [
      kind,
      `url(data:image/svg+xml;base64,${readFileSync(path.join(TEMPLATES, "_icons", `${kind}.svg`)).toString("base64")})`,
    ]),
  );
  const text = `/**
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
  labels: { readonly en?: Readonly<Record<string, string>>; readonly fr?: Readonly<Record<string, string>> };
  /** The density steps, loosest first: a version's \`fit_step\` is an index. */
  steps: { cv: readonly MirrorStep[]; letter: readonly MirrorStep[] };
  css: string;
}

export const TEMPLATE_MIRROR: Readonly<Record<string, MirroredTemplate>> = ${JSON.stringify(mirror)};

/** The contact line icons, \`--icon-<kind>\` of every page (templates.icon_css). */
export const TEMPLATE_ICONS: Readonly<Record<string, string>> = ${JSON.stringify(icons)};
`;
  // As `prettier --write` formats it: the repository has no prettier configuration.
  return format(text, { parser: "typescript", plugins: [estree, typescript] });
}

describe("the page drawn here mirrors the templates the renderer prints", () => {
  it("holds each template's style, steps, accent, photo and fonts as the api's folders state them", async () => {
    await expect(await mirrorSource()).toMatchFileSnapshot(MIRROR_FILE);
  });

  it("draws every template the api lists, the CV and the letter", () => {
    expect(slugs.length).toBeGreaterThan(0);
    expect(Object.keys(TEMPLATE_MIRROR).sort()).toEqual(slugs);
    for (const slug of slugs) {
      expect(LAYOUTS[slug], slug).toBeDefined();
      expect(typeof LAYOUTS[slug]?.cv, slug).toBe("function");
      expect(typeof LAYOUTS[slug]?.letter, slug).toBe("function");
    }
    expect(Object.keys(LAYOUTS).sort()).toEqual(slugs);
  });

  it("prints the headings, months and words of the templates' labels", () => {
    expect(PAGE_LABELS).toEqual(
      JSON.parse(read(TEMPLATES, "_base", "labels.json")),
    );
  });

  it("tells the stories with the thumbnails' invented person", () => {
    const sample = JSON.parse(
      read(TEMPLATES, "_base", "sample.json"),
    ) as Record<string, unknown>;
    delete sample._about;
    expect(TEMPLATE_SAMPLE).toEqual(sample);
  });

  it("loads the Latin faces the PDF embeds, from the same Fontsource release", () => {
    const source = read(FONTS_FILE);
    const imported = new Set(
      [
        ...source.matchAll(
          /"@fontsource\/([a-z0-9-]+)\/(latin-[0-9a-z-]+)\.css"/g,
        ),
      ].map((match) => `${match[1] ?? ""}/${match[2] ?? ""}`),
    );
    const families = [
      ...new Set(slugs.flatMap((slug) => manifest(slug).fonts)),
    ];
    const expected = new Set<string>();
    for (const family of families) {
      const data = JSON.parse(
        read(DOCUMENTS, "fonts", family, "family.json"),
      ) as {
        source: string;
        faces: { file: string }[];
      };
      const version = /@fontsource\/[a-z0-9-]+ ([0-9.]+)/.exec(
        data.source,
      )?.[1];
      expect(PACKAGE.dependencies[`@fontsource/${family}`], family).toBe(
        version,
      );
      for (const face of data.faces) {
        const match = /^latin-(\d+)-(normal|italic)\.woff2$/.exec(face.file);
        if (match)
          expected.add(
            `${family}/latin-${match[1] ?? ""}${match[2] === "italic" ? "-italic" : ""}`,
          );
      }
    }
    expect([...imported].sort()).toEqual([...expected].sort());
  });
});
