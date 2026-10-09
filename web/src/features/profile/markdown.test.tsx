import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { XSS_BODY } from "@/test/profile";

import { Markdown } from "./markdown";
import { parseBlocks } from "./markdown-parse";

declare global {
  interface Window {
    __xss?: string;
  }
}

afterEach(() => {
  delete window.__xss;
});

describe("Markdown", () => {
  it("renders headings, lists, quotes, code, bold, italic and links", () => {
    const { container } = render(
      <Markdown
        source={[
          "# Title",
          "",
          "Some **bold**, *italic* and `code`.",
          "",
          "- one",
          "- two",
          "  continued",
          "",
          "1. first",
          "2) second",
          "",
          "> quoted",
          "",
          "```",
          "<b>raw</b>",
          "```",
          "",
          "---",
          "[site](https://example.org)",
        ].join("\n")}
      />,
    );
    expect(screen.getByRole("heading", { name: "Title" }).tagName).toBe("H4");
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("italic");
    expect(container.querySelectorAll("ul > li")).toHaveLength(2);
    expect(container.querySelector("ul > li:last-child")?.textContent).toBe(
      "two continued",
    );
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
    expect(container.querySelector("blockquote")?.textContent).toBe("quoted");
    expect(container.querySelector("pre code")?.textContent).toBe("<b>raw</b>");
    expect(container.querySelector("b")).toBeNull();
    expect(container.querySelector("hr")).not.toBeNull();
    const link = screen.getByRole("link", { name: "site" });
    expect(link).toHaveAttribute("href", "https://example.org");
    expect(link).toHaveAttribute("rel", "noopener noreferrer nofollow");
  });

  it("shows HTML as text and never runs it", () => {
    const { container } = render(<Markdown source={XSS_BODY} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.textContent).toContain(
      '<script>window.__xss = "script"</script>',
    );
    expect(container.textContent).toContain("onerror=");
    expect(window.__xss).toBeUndefined();
  });

  it("keeps only http, https and mailto links", () => {
    render(
      <Markdown source="[a](javascript:alert(1)) [b](data:text/html,x) [c](mailto:x@example.org) [d](//evil.example)" />,
    );
    expect(screen.queryByRole("link", { name: "a" })).toBeNull();
    expect(screen.queryByRole("link", { name: "b" })).toBeNull();
    expect(screen.queryByRole("link", { name: "d" })).toBeNull();
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "c" })).toHaveAttribute(
      "href",
      "mailto:x@example.org",
    );
  });

  it("copes with empty, unterminated and odd input", () => {
    expect(parseBlocks("")).toEqual([]);
    expect(parseBlocks("```\nnever closed")).toEqual([
      { kind: "code", text: "never closed" },
    ]);
    expect(parseBlocks("####### seven")).toEqual([
      { kind: "paragraph", text: "####### seven" },
    ]);
    expect(parseBlocks("- a\n1. b")).toEqual([
      { kind: "list", ordered: false, items: ["a"] },
      { kind: "list", ordered: true, items: ["b"] },
    ]);
    expect(parseBlocks("a\r\nb\r\n\r\nc")).toEqual([
      { kind: "paragraph", text: "a b" },
      { kind: "paragraph", text: "c" },
    ]);
    render(<Markdown source={"**unclosed bold and _unclosed italic"} />);
    expect(
      screen.getByText("**unclosed bold and _unclosed italic"),
    ).toBeInTheDocument();
  });
});
