import { render, screen, within } from "@testing-library/react";
import ts from "typescript";
import { describe, expect, it } from "vitest";

import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function People({ stacked }: { stacked?: boolean }) {
  return (
    <Table stacked={stacked}>
      <TableCaption className="sr-only">People, by name</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Role</TableHead>
          <TableHead className="text-right">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow data-disabled>
          <TableHead>Ada Example</TableHead>
          <TableCell label="Role">Admin</TableCell>
          <TableCell>
            <button type="button">Open</button>
          </TableCell>
        </TableRow>
      </TableBody>
    </Table>
  );
}

describe("Table", () => {
  it("is named by its caption, with a scope on every header cell and a row header", () => {
    render(<People />);
    const table = screen.getByRole("table", { name: "People, by name" });
    const columns = within(table).getAllByRole("columnheader");
    expect(columns.map((head) => head.getAttribute("scope"))).toEqual([
      "col",
      "col",
      "col",
    ]);
    const header = within(table).getByRole("rowheader", {
      name: "Ada Example",
    });
    expect(header).toHaveAttribute("scope", "row");
    expect(
      within(table).getByRole("row", { name: /Ada Example/ }),
    ).toHaveAttribute("data-disabled");
  });

  it("stacked (the default): each row a card under 640px, each cell led by its label", () => {
    const { container } = render(<People />);
    const cell = screen.getByRole("cell", { name: "Admin" });
    expect(cell).toHaveAttribute("data-label", "Role");
    expect(cell.className).toContain(
      "max-sm:before:content-[attr(data-label)]",
    );
    // The column headers are read by screen readers only; the labels show instead.
    expect(container.querySelector("thead")?.className).toContain(
      "max-sm:sr-only",
    );
    const row = screen.getByRole("row", { name: /Ada Example/ });
    expect(row.className).toContain("max-sm:rounded-lg");
    expect(row.className).toContain("max-sm:bg-card");
    expect(screen.getByRole("rowheader").className).toContain(
      "max-sm:text-base",
    );
    // A cell without a label (the actions) has no label line.
    const actions = screen.getByRole("cell", { name: "Open" });
    expect(actions).not.toHaveAttribute("data-label");
    expect(actions.className).not.toContain("before:content");
  });

  it("stacked false: a comparison table keeps its columns and scrolls inside its border", () => {
    const { container } = render(<People stacked={false} />);
    const frame = container.querySelector('[data-slot="table-container"]');
    expect(frame?.className).toContain("overflow-x-auto");
    expect(frame?.className).not.toContain("sm:overflow-x-auto");
    expect(container.querySelector("thead")?.className).not.toContain(
      "sr-only",
    );
    expect(
      screen.getByRole("row", { name: /Ada Example/ }).className,
    ).not.toContain("max-sm:");
    expect(screen.getByRole("cell", { name: "Admin" }).className).not.toContain(
      "before:content",
    );
  });

  it("a busy table is dimmed and announced busy", () => {
    const { container, rerender } = render(
      <Table busy>
        <TableBody>
          <TableRow>
            <TableCell>One</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );
    const frame = container.querySelector('[data-slot="table-container"]');
    expect(frame).toHaveAttribute("aria-busy", "true");
    expect(frame?.className).toContain("opacity-60");
    rerender(
      <Table>
        <TableBody>
          <TableRow>
            <TableCell>One</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );
    expect(frame).not.toHaveAttribute("aria-busy", "true");
    expect(frame?.className).not.toContain("opacity-60");
  });

  it("uses theme tokens only, so light and dark both follow the theme", () => {
    const { container } = render(<People />);
    expect(container.innerHTML).not.toMatch(
      /(bg|text|border)-(white|black|gray|slate|zinc|neutral|stone)\b|#[0-9a-f]{3,6}\b/i,
    );
  });
});

// Every table of the app goes through this component (owner, 2026-11-05: "verify that you use
// the same component array than all over the app"): no source file but this one draws table
// markup itself. Tests may, to build fixtures.
const sources = import.meta.glob<string>(
  ["/src/**/*.tsx", "!/src/**/*.test.tsx", "!/src/components/ui/table.tsx"],
  { eager: true, query: "?raw", import: "default" },
);
const TABLE_TAGS = new Set([
  "table",
  "caption",
  "colgroup",
  "col",
  "thead",
  "tbody",
  "tfoot",
  "tr",
  "th",
  "td",
]);

function tableTags(path: string, text: string): string[] {
  const file = ts.createSourceFile(
    path,
    text,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName.getText(file);
      if (TABLE_TAGS.has(tag)) found.push(`${path}: <${tag}>`);
    }
    if (
      ts.isCallExpression(node) &&
      node.expression.getText(file).endsWith("createElement") &&
      node.arguments[0] &&
      ts.isStringLiteral(node.arguments[0]) &&
      TABLE_TAGS.has(node.arguments[0].text)
    )
      found.push(`${path}: createElement("${node.arguments[0].text}")`);
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

describe("one table component for the whole app", () => {
  it("finds the sources it checks", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(50);
    expect(Object.keys(sources)).toContain(
      "/src/features/admin/components/users-section.tsx",
    );
  });

  it("finds table markup when a file draws it", () => {
    expect(
      tableTags("x.tsx", "const x = <table><tr><td>1</td></tr></table>;"),
    ).toEqual(["x.tsx: <table>", "x.tsx: <tr>", "x.tsx: <td>"]);
    expect(tableTags("y.tsx", 'React.createElement("th", {});')).toEqual([
      'y.tsx: createElement("th")',
    ]);
    // A word in a string or a comment is not markup.
    expect(tableTags("z.tsx", 'const s = "<table>"; // <td>')).toEqual([]);
  });

  it("no file but components/ui/table.tsx draws a table", () => {
    const found = Object.entries(sources).flatMap(([path, text]) =>
      tableTags(path, text),
    );
    expect(found).toEqual([]);
  });
});
