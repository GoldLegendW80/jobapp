import ts from "typescript";
import { describe, expect, it } from "vitest";

// Owning lanes add their page here when their O10 follow-up lands. Prose and data from the
// catalogue remain plain English; static control labels must use the shared word list.
const pages = import.meta.glob<string>(
  [
    "../pages/deck-page.tsx",
    "../components/deck/filter-row.tsx",
    "../components/deck/swipe-card.tsx",
    "../components/deck/posting-sheet.tsx",
    "../../onboarding/components/steps/step-profile.tsx",
    "../../onboarding/components/steps/step-looking.tsx",
    "../../onboarding/components/steps/step-where.tsx",
    "../../onboarding/components/steps/step-work.tsx",
    "../../onboarding/components/steps/step-pay.tsx",
    "../../onboarding/components/steps/pay-fields.tsx",
    "../../onboarding/components/steps/step-apply.tsx",
    "../../onboarding/components/steps/step-summary.tsx",
    "../../onboarding/components/wizard/wizard.tsx",
    "../../onboarding/components/wizard/wizard-progress.tsx",
    "../../onboarding/components/wizard/wizard-nav.tsx",
    "../../onboarding/pages/welcome-page.tsx",
  ],
  { eager: true, query: "?raw", import: "default" },
);
const labelTags = new Set([
  "Button",
  "Link",
  "Label",
  "label",
  "FieldLabel",
  "legend",
  "FieldLegend",
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
