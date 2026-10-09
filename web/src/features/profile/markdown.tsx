/**
 * A small Markdown renderer for the fact base, safe by construction: it builds React elements
 * from the text and never sets HTML, so a `<script>` or an `onerror` attribute in a block shows
 * as the characters typed and never runs. Links keep only http, https and mailto addresses.
 *
 * Supported: ATX headings, paragraphs, unordered and ordered lists (one level, continuation
 * lines joined), blockquotes, fenced code, horizontal rules; inline code, bold, italic and links.
 */
import type { ReactNode } from "react";
import { parseBlocks } from "./markdown-parse";

const INLINE =
  /(`[^`]+`)|(\*\*[^*]+\*\*|__[^_]+__)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(\[[^\]]+\]\([^)\s]+\))/;
const SAFE_HREF = /^(https?:\/\/|mailto:)/i;

function renderInline(text: string, keyPrefix = "i"): ReactNode[] {
  const nodes: ReactNode[] = [];
  let rest = text;
  let n = 0;
  while (rest.length > 0) {
    const match = INLINE.exec(rest);
    if (!match) {
      nodes.push(rest);
      break;
    }
    if (match.index > 0) nodes.push(rest.slice(0, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${n}`;
    n += 1;
    if (match[1]) {
      nodes.push(
        <code key={key} className="rounded bg-muted px-1 py-0.5 text-[0.9em]">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (match[2]) {
      nodes.push(
        <strong key={key}>{renderInline(token.slice(2, -2), key)}</strong>,
      );
    } else if (match[3]) {
      nodes.push(<em key={key}>{renderInline(token.slice(1, -1), key)}</em>);
    } else {
      const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token);
      const label = link?.[1] ?? token;
      const href = link?.[2] ?? "";
      nodes.push(
        SAFE_HREF.test(href) ? (
          <a
            key={key}
            href={href}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="underline underline-offset-2"
          >
            {label}
          </a>
        ) : (
          <span key={key}>{label}</span>
        ),
      );
    }
    rest = rest.slice(match.index + token.length);
  }
  return nodes;
}

export function Markdown({ source }: { source: string }) {
  const blocks = parseBlocks(source);
  return (
    <div className="flex flex-col gap-3 text-sm leading-relaxed break-words">
      {blocks.map((block, index) => {
        const key = `b-${index}`;
        switch (block.kind) {
          case "heading": {
            // Inside a section that already has an h2/h3: start at h4.
            const level = Math.min(6, block.level + 3);
            const Tag = `h${level}` as "h4" | "h5" | "h6";
            return (
              <Tag key={key} className="font-semibold">
                {renderInline(block.text, key)}
              </Tag>
            );
          }
          case "paragraph":
            return <p key={key}>{renderInline(block.text, key)}</p>;
          case "list": {
            const items = block.items.map((item, j) => (
              <li key={`${key}-${j}`}>{renderInline(item, `${key}-${j}`)}</li>
            ));
            return block.ordered ? (
              <ol key={key} className="list-decimal space-y-1 pl-5">
                {items}
              </ol>
            ) : (
              <ul key={key} className="list-disc space-y-1 pl-5">
                {items}
              </ul>
            );
          }
          case "quote":
            return (
              <blockquote
                key={key}
                className="border-l-2 pl-3 text-muted-foreground"
              >
                {renderInline(block.text, key)}
              </blockquote>
            );
          case "code":
            return (
              <pre
                key={key}
                className="overflow-x-auto rounded-md bg-muted p-3 text-xs"
              >
                <code>{block.text}</code>
              </pre>
            );
          case "rule":
            return <hr key={key} />;
        }
      })}
    </div>
  );
}
