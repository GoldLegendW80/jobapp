/** Block structure of the Markdown the fact base uses; rendered by markdown.tsx. */

export type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "quote"; text: string }
  | { kind: "code"; text: string }
  | { kind: "rule" };

const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d{1,9}[.)]\s+(.*)$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const FENCE = /^\s*(```|~~~)/;

const isQuote = (line: string) => line.trimStart().startsWith(">");
const isItem = (line: string) => BULLET.test(line) || NUMBERED.test(line);

export function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const at = (index: number) => lines[index] ?? "";
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = at(i);
    if (line.trim() === "") {
      i += 1;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1] ?? "```";
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !at(i).trim().startsWith(marker)) {
        body.push(at(i));
        i += 1;
      }
      i += 1; // the closing fence, or past the end
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({
        kind: "heading",
        level: (heading[1] ?? "#").length,
        text: heading[2] ?? "",
      });
      i += 1;
      continue;
    }
    if (RULE.test(line)) {
      blocks.push({ kind: "rule" });
      i += 1;
      continue;
    }
    if (isQuote(line)) {
      const body: string[] = [];
      while (i < lines.length && isQuote(at(i))) {
        body.push(at(i).trimStart().replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push({ kind: "quote", text: body.join(" ") });
      continue;
    }
    if (isItem(line)) {
      const ordered = NUMBERED.test(line);
      const marker = ordered ? NUMBERED : BULLET;
      const items: string[] = [];
      while (i < lines.length && at(i).trim() !== "") {
        const item = marker.exec(at(i));
        if (item) items.push(item[1] ?? "");
        else if (isItem(at(i)))
          break; // a list of the other kind starts
        else if (items.length > 0)
          items[items.length - 1] =
            `${items[items.length - 1] ?? ""} ${at(i).trim()}`;
        i += 1;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }
    const body: string[] = [];
    while (
      i < lines.length &&
      at(i).trim() !== "" &&
      !HEADING.test(at(i)) &&
      !FENCE.test(at(i)) &&
      !isItem(at(i)) &&
      !isQuote(at(i))
    ) {
      body.push(at(i).trim());
      i += 1;
    }
    blocks.push({ kind: "paragraph", text: body.join(" ") });
  }
  return blocks;
}
