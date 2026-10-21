import ts from "typescript";
import { describe, expect, it } from "vitest";

// Owning lanes add their page here when their O10 follow-up lands. Prose and data from the
// catalogue remain plain English; static control labels must use the shared word list.
const pages = import.meta.glob<string>(
  [
    "./deck/deck-page.tsx",
    "./deck/filter-row.tsx",
    "./deck/swipe-card.tsx",
    "./deck/posting-sheet.tsx",
    "./sources/freelance-sources-card.tsx",
    "../onboarding/step-panels.tsx",
    "../onboarding/welcome-page.tsx",
    "../onboarding/starter-boards.tsx",
  ],
  { eager: true, query: "?raw", import: "default" },
);
const labelTags = new Set([
  "Button",
  "Link",
  "Label",
  "label",
  "legend",
  "summary",
  "option",
  "h1",
  "h2",
  "h3",
  "h4",
  "SheetTitle",
]);
const labelProps = new Set(["label", "legend", "title", "aria-label"]);

describe("simple page wording (O10)", () => {
  it.each(Object.entries(pages))(
    "%s takes static labels from wording.ts",
    (name, source) => {
      const file = ts.createSourceFile(
        name,
        source,
        ts.ScriptTarget.Latest,
        true,
        ts.ScriptKind.TSX,
      );
      const literals: string[] = [];
      function visit(node: ts.Node) {
        if (ts.isJsxText(node) && ts.isJsxElement(node.parent)) {
          const tag = node.parent.openingElement.tagName.getText(file);
          if (labelTags.has(tag) && node.text.trim())
            literals.push(node.text.trim());
        }
        if (
          ts.isJsxAttribute(node) &&
          labelProps.has(node.name.getText(file)) &&
          node.initializer &&
          ts.isStringLiteral(node.initializer)
        ) {
          literals.push(node.initializer.text);
        }
        ts.forEachChild(node, visit);
      }
      visit(file);
      expect(literals, "Move these control labels to wording.ts").toEqual([]);
      expect(source).toMatch(/@\/lib\/wording/);
    },
  );
});
